"""Generate symmetrized force constants using compact projection matrix."""
import itertools
import time
from typing import Optional

import numpy as np
import scipy
from scipy.sparse import coo_array, csc_array, csr_array

from symfc.matrix_funcs import (
    convert_basis_sets_matrix_form,
    get_projector_sum_rule,
    get_spg_proj_c,
    to_serial,
)


class FCBasisSetsCompact:
    """Compact symmetry adapted basis sets for force constants.

    The strategy is as follows:
    Construct compression matrix using permutation symmetry C.
    The matrix shape is (NN33, N(N+1)/2).
    This matrix expands elements of upper right triagle to
    full elements NN33 of matrix. (C @ C.T) is made to be identity matrix.
    The projection matrix of space group operations is multipiled by C
    from both side, and the resultant matrix is diagonalized.
    The eigenvectors thus obtained are tricky further applying constraints
    of translational symmetry.

    """

    def __init__(
        self,
        reps: list[coo_array],
        log_level: int = 0,
    ):
        """Init method.

        Parameters
        ----------
        reps : list[coo_array]
            Matrix representations of symmetry operations.
        log_level : int, optional
            Log level. Default is 0.

        """
        self._reps: list[coo_array] = reps
        self._log_level = log_level

        self._natom = int(round(self._reps[0].shape[0] / 3))

        self._basis_sets: Optional[np.ndarray] = None

        self._run()

    @property
    def basis_sets_matrix_form(self) -> Optional[list[np.ndarray]]:
        """Retrun a list of FC basis in 3Nx3N matrix."""
        if self._basis_sets is None:
            return None

        return convert_basis_sets_matrix_form(self._basis_sets)

    @property
    def basis_sets(self) -> Optional[np.ndarray]:
        """Return a list of FC basis in (N, N, 3, 3) dimentional arrays."""
        return self._basis_sets

    def _run(self, tol: float = 1e-8):
        t0 = time.time()
        perm_mat = _get_permutation_compression_matrix(self._natom)
        t2 = time.time()
        print(f"|--- {t2 - t0} ---")
        vecs = self._step2(tol=tol)
        t3 = time.time()
        print(f"|--- {t3 - t0} ---")
        U = self._step3(vecs, perm_mat)
        t4 = time.time()
        print(f"|--- {t4 - t0} ---")
        self._step4(U, perm_mat, tol=tol)
        t5 = time.time()
        print(f"|--- {t5 - t0} ---")

    def _step2(self, tol: float = 1e-8) -> csr_array:
        t0 = time.time()
        row, col, data = get_spg_proj_c(self._reps, self._natom)
        t1 = time.time()
        print(f"  |--- {t1 - t0} ---")
        size = self._natom * 3 * (self._natom * 3 + 1)
        size = size // 2
        perm_spg_mat = csc_array((data, (row, col)), shape=(size, size), dtype="double")
        t2 = time.time()
        print(f"  |--- {t2 - t0} ---")
        rank = int(round(perm_spg_mat.diagonal(k=0).sum()))
        print(f"Solving eigenvalue problem of projection matrix (rank={rank}).")
        vals, vecs = scipy.sparse.linalg.eigsh(perm_spg_mat, k=rank, which="LM")
        nonzero_elems = np.nonzero(np.abs(vals) > tol)[0]
        vals = vals[nonzero_elems]
        # Check non-zero values are all ones. This is a weak check of commutativity.
        np.testing.assert_allclose(vals, 1.0, rtol=0, atol=tol)
        vecs = vecs[:, nonzero_elems]
        if self._log_level:
            print(f" eigenvalues of projector = {vals}")
        t3 = time.time()
        print(f"  |--- {t3 - t0} ---")
        return vecs

    def _step3(self, vecs: csr_array, perm_mat: csr_array) -> csr_array:
        U = perm_mat @ vecs
        U = get_projector_sum_rule(self._natom) @ U
        U = perm_mat.T @ U
        return U

    def _step4(self, U: csr_array, perm_mat: csr_array, tol: float = 1e-8):
        # Note: proj_trans and (perm_mat @ perm_mat.T) are considered not commute.
        # for i in range(30):
        #     U = perm_mat.T @ (proj_trans @ (perm_mat @ U))
        U, s, _ = np.linalg.svd(U, full_matrices=False)
        # Instead of making singular value small by repeating, just removing
        # non one eigenvalues.
        U = U[:, np.where(np.abs(s) > 1 - tol)[0]]

        if self._log_level:
            print(f"  - svd eigenvalues = {np.abs(s)}")
            print(f"  - basis size = {U.shape}")

        fc_basis = [
            b.reshape((self._natom, self._natom, 3, 3)) for b in (perm_mat @ U).T
        ]
        self._basis_sets = np.array(fc_basis, dtype="double", order="C")


def _get_permutation_compression_matrix(natom: int) -> csr_array:
    """Return compression matrix by permutation matrix.

    Matrix shape is (NN33,(N*3)((N*3)+1)/2).
    Non-zero only ijab and jiba column elements for ijab rows.
    Rows upper right NN33 matrix elements are selected for rows.

    """
    col, row, data = [], [], []
    val = np.sqrt(2) / 2
    size_sq = natom**2 * 9

    n = 0
    for ia, jb in itertools.combinations_with_replacement(range(natom * 3), 2):
        i_i = ia // 3
        i_a = ia % 3
        i_j = jb // 3
        i_b = jb % 3
        col.append(n)
        row.append(to_serial(i_i, i_a, i_j, i_b, natom))
        if i_i == i_j and i_a == i_b:
            data.append(1)
        else:
            data.append(val)
            col.append(n)
            row.append(to_serial(i_j, i_b, i_i, i_a, natom))
            data.append(val)
        n += 1
    if (natom * 3) % 2 == 1:
        assert (natom * 3) * ((natom * 3 + 1) // 2) == n, f"{natom}, {n}"
    else:
        assert ((natom * 3) // 2) * (natom * 3 + 1) == n, f"{natom}, {n}"
    return csr_array((data, (row, col)), shape=(size_sq, n), dtype="double")
