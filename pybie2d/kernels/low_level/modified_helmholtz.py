import numpy as np
import numexpr as ne
import numba
import scipy as sp
import scipy.special
import warnings

from ... import have_fmm
if have_fmm:
    from ... import FMM
from ...misc.numba_special_functions import _numba_k0, _numba_k1, numba_k0, numba_k1

################################################################################
# Greens function and derivative for Modified Helmholtz Equation

# these are both off by a factor of 1/(2*np.pi)
def Modified_Helmholtz_Greens_Function(r, k):
    return numba_k0(k*r)
def Modified_Helmholtz_Greens_Function_Derivative(r, k):
    # note this needs to be multiplied by coordinate you are taking derivative
    # with respect to, e.g.:
    #   d/dx G(r, k) = x*GD(r,k)
    return k*numba_k1(k*r)/r

################################################################################
# General Purpose Low Level Source --> Target Kernel Apply Functions

# for now there are no numba jitted heat kernels
# need to figure out how to compute bessel functions in a compatible way

@numba.njit(parallel=True)
def _modified_helmoholtz(sx, sy, tx, ty, charge, dipstr, nx, ny, pot, ifcharge, ifdipole, k):
    doself = sx is tx and sy is ty
    for i in numba.prange(tx.shape[0]):
        for j in range(sx.shape[0]):
            if not (doself and i == j):
                dx = tx[i] - sx[j]
                dy = ty[i] - sy[j]
                r = np.sqrt(dx**2 + dy**2)
                if ifdipole:
                    n_dot_d = nx[j]*dx + ny[j]*dy
                    pot[i] += n_dot_d*k*_numba_k1(k*r)/r*dipstr[j]
                if ifcharge:
                    pot[i] += _numba_k0(k*r)*charge[j]

def Modified_Helmholtz_Kernel_Apply_numba(source, target, k=1.0, charge=None,
                                        dipstr=None, dipvec=None, weights=None):
    """
    Interface to numba-jitted Modified Helmholtz Kernel
    Inputs:
        source,   required, float(2, ns),  source coordinates
        target,   required, float(2, nt),  target coordinates
        charge,   optional, float(ns),     charge at source locations
        dipstr,   optional, float(ns),     dipole strength at source locations
        dipvec,   optional, float(2, ns),  dipole orientation at source loc
        weights,  optional, float(ns),     quadrature weights
        gradient, optional, bool,          whether to compute gradient or not
    Outputs:
        float(nt), potential at target coordinates
    ns = number of source points; nt = number of target points
    """
    weights = 1.0 if weights is None else weights
    weighted_weights = 0.5*weights/np.pi
    sx = source[0]
    sy = source[1]
    tx = target[0]
    ty = target[1]
    ifcharge = charge is not None
    ifdipole = dipstr is not None
    pot = np.zeros(target.shape[1], dtype=float)
    zero_vec = np.zeros(source.shape[1], dtype=float)
    ch = zero_vec if charge is None else charge*weighted_weights
    ds = zero_vec if dipstr is None else dipstr*weighted_weights
    nx = zero_vec if dipvec is None else dipvec[0]
    ny = zero_vec if dipvec is None else dipvec[1]
    _modified_helmoholtz(sx, sy, tx, ty, ch, ds, nx, ny, pot, ifcharge, ifdipole, k)
    return pot

def Modified_Helmholtz_Kernel_Apply_FMM(source, target, k, charge=None,
                                        dipstr=None, dipvec=None, weights=None):
    """
    Interface to FMM Laplace Kernels
    Inputs:
        source,   required, float(2, ns),  source coordinates
        target,   required, float(2, nt),  target coordinates
        charge,   optional, float(ns),     charge at source locations
        dipstr,   optional, float(ns),     dipole strength at source locations
        dipvec,   optional, float(2, ns),  dipole orientation at source loc
        weights,  optional, float(ns),     quadrature weights
    Outputs:
        float(nt), potential at target coordinates
    ns = number of source points; nt = number of target points
    """
    weights = 1.0 if weights is None else weights
    ch = charge*weights if charge is not None else None
    ds = dipstr*weights if dipstr is not None else None
    if source is target:
        out = FMM(kind='helmholtz', source=source, charge=ch,
                    dipstr=ds, dipvec=dipvec, compute_source_potential=True,
                    helmholtz_parameter=1j*k)['source']
    else:
        out = FMM(kind='helmholtz', source=source, target=target, charge=ch,
                    dipstr=ds, dipvec=dipvec, compute_target_potential=True,
                    helmholtz_parameter=1j*k)['target']
    return out['u'].real

Modified_Helmholtz_Kernel_Applys = {}
Modified_Helmholtz_Kernel_Applys['numba'] = Modified_Helmholtz_Kernel_Apply_numba
Modified_Helmholtz_Kernel_Applys['FMM']   = Modified_Helmholtz_Kernel_Apply_FMM

def Modified_Helmholtz_Kernel_Apply(source, target, k, charge=None, dipstr=None, dipvec=None,
                                weights=None, gradient=False, backend='numba'):
    """
    Laplace Kernel Apply
    Inputs:
        source,   required, float(2, ns),  source coordinates
        target,   required, float(2, nt),  target coordinates
        charge,   optional, float(ns),     charge at source locations
        dipstr,   optional, float(ns),     dipole strength at source locations
        dipvec,   optional, float(2, ns),  dipole orientation at source loc
        weights,  optional, float(ns),     quadrature weights
        gradient, optional, bool,          whether to compute gradient or not
        backend,  optional, str,           backend ('FMM' or 'numba')
    Outputs:
        if gradient == False:
            float(nt), potential at target coordinates
        if gradient == True:
            tuple of:
                float(nt), potential at target coordinates
                float(nt), x-derivative of potential at target coordinates
                float(nt), y-derivative of potential at target coordinates
    ns = number of source points; nt = number of target points
    """
    return Modified_Helmholtz_Kernel_Applys[backend](source, target, k, charge, dipstr,
                                                                dipvec, weights)

################################################################################
# General Purpose Low Level Source --> Target Kernel Formation

def Modified_Helmholtz_Kernel_Form(source, target, k=1.0, ifcharge=False,
                                    ifdipole=False, dipvec=None, weights=None):
    """
    Modified Helmholtz Kernel Formation
        for the problem (Delta - k^2)u = 0
    Computes the matrix:
        [ chweight*G_ij + dpweight*(n_j dot grad G_ij) ] weights_j
        where G is the Modified Helmholtz Greens function
            (G(z) = k^2*k0(k*z)/(2*pi))
        and other parameters described below
    Also returns the matrices for the x and y derivatives, if requested

    Parameters:
        source,   required, float(2, ns),  source coordinates
        target,   required, float(2, nt),  target coordinates
        k,        required, float,         modified helmholtz parameter
        ifcharge, optional, bool,          include charge contribution
        chweight, optional, float,         scalar weight to apply to charges
        ifdipole, optional, bool,          include dipole contribution
        dpweight, optional, float,         scalar weight to apply to dipoles
        dipvec,   optional, float(2, ns),  dipole orientations
        weights,  optional, float(ns),     quadrature weights

    This function assumes that source and target have no coincident points
    """
    ns = source.shape[1]
    nt = target.shape[1]
    SX = source[0]
    SY = source[1]
    TX = target[0][:,None]
    TY = target[1][:,None]
    if dipvec is not None:
        nx = dipvec[0]
        ny = dipvec[1]
    scale = 1.0/(2*np.pi)
    scale = scale*np.ones(ns) if weights is None else scale*weights
    G = np.zeros([nt, ns], dtype=float)
    if not (ifcharge or ifdipole):
        # no charges, no dipoles, just return appropriate zero matrix
        return G
    else:
        dx = ne.evaluate('TX - SX')
        dy = ne.evaluate('TY - SY')
        r = ne.evaluate('sqrt(dx**2 + dy**2)')
        if ifcharge:
            GC = Modified_Helmholtz_Greens_Function(r, k)
            ne.evaluate('G + GC', out=G)
        if ifdipole:
            GD = Modified_Helmholtz_Greens_Function_Derivative(r, k)
            # dipoles effect on potential
            ne.evaluate('G + (nx*dx + ny*dy)*GD', out=G)
        if source is target:
            np.fill_diagonal(G, 0.0)
        return ne.evaluate('G*scale', out=G)