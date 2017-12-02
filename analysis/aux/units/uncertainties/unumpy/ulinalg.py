"""
This module provides uncertainty-aware functions that generalize some
of the functions from numpy.linalg.

(c) 2010 by Eric O. LEBIGOT (EOL) <eric.lebigot@normalesup.org>.
"""

from analysis.aux.units.uncertainties import __author__
from . import core

# This module cannot import unumpy because unumpy imports this module.

__all__ = ['inv', 'pinv']

inv = core._inv
pinv = core._pinv

