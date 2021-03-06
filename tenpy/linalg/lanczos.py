"""Lanczos implementation for np_conserved arrays."""
# Copyright 2018 TeNPy Developers

from . import np_conserved as npc
from ..tools.params import get_parameter
import numpy as np
import warnings


def gram_schmidt(vecs, rcond=1.e-14, verbose=0):
    """In place Gram-Schmidt Orthogonalization and normalization for npc Arrays.

    Parameters
    ----------
    vecs : list of :class:`~tenpy.linalg.np_conserved.Array`
        The vectors which should be orthogonalized. Entries are modified *in place*.
        if a norm < rcond, they entry is set to `None`
    rcond : float
        Vectors of ``norm < rcond`` (after projecting out previous vectors) are discarded.
    verbose : int
        Print additional output if verbose >= 1.

    Returns
    -------
    vecs : list of Array
        The ortho-normalized vectors (without any ``None``).
    ov : 2D Array
        For ``j >= i``, ``ov[j, i] = npc.inner(vecs[j], vecs[i], do_conj=True)``
        (where vecs[j] was orthogonalized to all ``vecs[k], k < i``).
    """
    k = len(vecs)
    ov = np.zeros((k, k), dtype=vecs[0].dtype)
    for j in range(k):
        n = ov[j, j] = npc.norm(vecs[j])
        if n > rcond:
            vecs[j] *= 1. / n
            for i in range(j + 1, k):
                ov[j, i] = ov_ji = npc.inner(vecs[j], vecs[i], do_conj=True)
                vecs[i] -= ov_ji * vecs[j]
        else:
            if verbose >= 1:
                print("GramSchmidt: Rank defficient", n)
            vecs[j] = None
    vecs = [q for q in vecs if q is not None]
    if verbose >= 1:
        k = len(vecs)
        G = np.empty((k, k), dtype=vecs[0].dtype)
        for i, v in enumerate(vecs):
            for j, w in enumerate(vecs):
                G[i, j] = npc.inner(v, w, do_conj=True)
        print("GramSchmidt:", k, np.diag(ov), np.linalg.norm(G - np.eye(k)))
    return vecs, ov


def lanczos(A, psi, lanczos_params={}, orthogonal_to=[]):
    """Lanczos Algorithm for finding the lowest Eigenvector.

    Parameters
    ----------
    A : :class:`~tenpy.linalg.sparse.LinearOperator`-like
        A hermitian linear operator. Must implement the method `matvec` acting on a
        :class:`~tenpy.linalg.np_conserved.Array`; nothing else required.
    psi : :class:`~tenpy.linalg.np_conserved.Array`
        The starting vector. Should be the best guess available.
    lanczos_params : dict
        Further optional parameters as described in the following table.
        Use ``verbose=1`` to print the used parameters during runtime.

        ======= ====== ===============================================================
        key     type   description
        ======= ====== ===============================================================
        N_min   int    Minimum number of steps to perform.
        ------- ------ ---------------------------------------------------------------
        N_max   int    Maximum number of steps to perform.
        ------- ------ ---------------------------------------------------------------
        E_tol   float  Stop if energy difference per step < `E_tol`
        ------- ------ ---------------------------------------------------------------
        P_tol   float  Tolerance for the error estimate from the
                       Ritz Residual, stop if ``(RitzRes/gap)**2 < P_tol``
        ------- ------ ---------------------------------------------------------------
        min_gap float  Lower cutoff for the gap estimate used in the P_tol criterion.
        ------- ------ ---------------------------------------------------------------
        N_cache int    The maximum number of `psi` to keep in memory.
        ======= ====== ===============================================================

        The algorithm stops if *both* criteria for `e_tol` and `p_tol` are met
        or if the maximum number of steps was reached.

    orthogonal_to : A list of :class:`~tenpy.linalg.np_conserved.Array`
        Vectors (same tensor structure as psi) Lanczos will orthogonalize against,
        ensuring that the result is perpendicular to them.

    Returns
    -------
    E0 : float
        Ground state energy (estimate).
    psi0 : :class:`~tenpy.linalg.np_conserved.Array`
        Ground state vector (estimate).
    N : int
        Number of steps performed.
        The results are optimal in the

    Notes
    -----
    I have computed the Ritz residual (RitzRes) according to
    http://web.eecs.utk.edu/~dongarra/etemplates/node103.html#estimate_residual.
    Given the gap, the Ritz residual gives a bound on the error in the wavefunction,
    ``err < (RitzRes/gap)**2``.
    I estimate the gap from the full Lanczos spectrum.


    .. todo :
        Even the Wikipedia page contains a warning that one can quickly loose orthogonality.
        Should we include a way of Re-orthogonalization?
        At least orthogonalize against the cached states?
        (it should be much faster than applying A)
    """
    verbose = lanczos_params.get('verbose', 0)
    if len(orthogonal_to) > 0:
        orthogonal_to, _ = gram_schmidt(orthogonal_to, verbose / 10)
    N_cache = get_parameter(lanczos_params, 'N_cache', 6, "Lanczos")
    if N_cache < 2:
        raise ValueError("Need to cache at least two vectors.")
    cache = []

    N_min = get_parameter(lanczos_params, 'N_min', 2, "Lanczos")
    N_max = get_parameter(lanczos_params, 'N_max', 20, "Lanczos")
    E_tol = get_parameter(lanczos_params, 'E_tol', 5.e-15, "Lanczos")
    P_tol = get_parameter(lanczos_params, 'P_tol', 1.e-14, "Lanczos")
    min_gap = get_parameter(lanczos_params, 'min_gap', 1.e-12, "Lanczos")
    Delta_E0 = 2.
    P_err = 2.
    Es = []

    # First Lanczos iteration: Form tridiagonal form of A in the Krylov subspace, stored in T
    T = np.zeros([N_max + 1, N_max + 1], dtype=np.float)
    ULP = 5.e-15  # Cutoff (ULP=unit last place) to abort if beta (= norm of next v) is too small.
    # This is necessary if the rank of A is smaller than N_max - then we get a complete
    # basis of the Krylov space, and beta will be zero.
    above_ULP = True
    w = psi  # initialize
    beta = npc.norm(w)
    for k in range(N_max):
        w /= beta
        _to_cache(w, cache, N_cache)
        w = cache[-1].copy()
        # project out the orthogonal parts:
        # equivalent to using A' = P A P
        for o in orthogonal_to:  # Project out
            w -= o * npc.inner(o, w, do_conj=True)
        w = A.matvec(w)
        for o in orthogonal_to[::-1]:  # reverse: more obviously Hermitian.
            w -= o * npc.inner(o, w, do_conj=True)
        alpha = np.real(npc.inner(w, cache[-1], do_conj=True)).item()
        T[k, k] = alpha
        if k > 0:
            w -= beta * cache[-2]
        w -= alpha * cache[-1]
        beta = npc.norm(w)
        above_ULP = abs(beta) > ULP
        if above_ULP:
            T[k, k + 1] = T[k + 1, k] = beta

        # Diagonalize T
        if k == 0:
            E_T = [alpha]
        else:
            E_T, v_T = np.linalg.eigh(T[0:k + 1, 0:k + 1])  # returns eigenvalues sorted ascending
            RitzRes = np.abs(v_T[k, 0] * T[k, k + 1])
            Delta_E0 = (Es[-1][0] - E_T[0])
            gap = max(E_T[1] - E_T[0], min_gap)
            P_err = (RitzRes / gap)**2
        Es.append(E_T)
        if not above_ULP or (k + 1 >= N_min and (P_err < P_tol or Delta_E0 < E_tol)):
            break
    N = k + 1  # == len(Es)
    if verbose >= 1:
        if verbose >= 10:
            _plot_stats(Es)
        if k > 1:
            print(''.join([
                "Lanczos N={0:d}, gap={1:.3e} ".format(N, gap),
                "| DeltaE0={0:.3e} E_tol={1:e} ".format(Delta_E0, E_tol),
                "| P_err={0:.3e} P_tol={1:e}".format(P_err, P_tol)
            ]))
        else:
            print("Lanczos N={0:d}, alpha={1:.3e}, beta={2:.3e}".format(N, alpha, beta))

    if N == 1:
        return E_T[0], psi.copy(), N  # no better estimate available

    # Second Lanczos iteration.
    # Now that we know the (Ritz) eigenvector's coefficients v_T[:, 0] in the Krylov subspace,
    # construct the actual vector ``psi0 = sum_k  v_T[k, 0] vec[k]``,
    # where ``vec[k]`` is the k-th vector of the iteration.

    psi0 = psi * v_T[0, 0]  # the start vector is still known
    # and the last len(cache) vectors have been cached
    for k in range(1, min(len(cache) + 1, N)):
        psi0 += v_T[N - k, 0] * cache[-k]
    len_cache = len(cache)
    del cache  # free memory: we need at least two more vectors
    # other vectors are not cached, so we need to restart the Lanczos iteration.
    q0 = None
    q1 = psi  # start vector; normalized above in place
    for k in range(0, N - len_cache - 1):
        w = q1.copy()
        for o in orthogonal_to:  # Project out
            w -= o * npc.inner(o, w, do_conj=True)
        w = A.matvec(w)
        for o in orthogonal_to[::-1]:  # reverse: more obviously Hermitian.
            w -= o * npc.inner(o, w, do_conj=True)
        if k > 0:
            w -= beta * q0
        alpha = T[k, k]
        w -= alpha * q1
        beta = T[k, k + 1]
        w /= beta
        q0 = q1
        q1 = w
        psi0 += q1 * v_T[k + 1, 0]
    psi0_norm = npc.norm(psi0)
    if abs(1. - psi0_norm) > 1.e-3:
        warnings.warn("poorly conditioned Lanczos: |psi_0| = {0:f}".format(psi0_norm))
    psi0 /= psi0_norm
    if verbose >= 1. and len(orthogonal_to) > 0:
        print(''.join(["Lanczos orthogonality:"] + [
            " {0:.3e}".format(np.abs(npc.inner(o, psi0, do_conj=True))) for o in orthogonal_to
        ]))
    return E_T[0], psi0, N


def _to_cache(psi, cache, N):
    """FIFO (first in first out) cache of at most N entries."""
    cache.append(psi)
    if len(cache) > N:
        cache.pop(0)


def _plot_stats(Es):
    import matplotlib.pyplot as plt
    ks = [[k] * len(E) for k, E in enumerate(Es)]
    ks = np.array(sum(ks, []))
    Es = np.array(sum([list(E) for E in Es], []))
    plt.scatter(ks, np.array(Es))
    plt.xlabel("Lanczos step")
    plt.ylabel("Ritz Values (= energy estimates)")
    plt.show()
