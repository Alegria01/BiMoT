# -*- coding: utf-8 -*-

#-- standard libraries
import itertools
import functools
import collections
import inspect
import re
import os
import sys
import logging
import urllib
import numpy as np
import pytz
import datetime

#-- optional libraries: WARNING: when these modules are not installed, the
#   module's use is restricted
try: import ephem
except ImportError: print("Unable to load pyephem, stellar coordinate transformations unavailable")

#-- from IVS repository
from analysis.aux.units import constants
from analysis.aux.units.uncertainties import unumpy,AffineScalarFunc,ufloat
from analysis.aux.units.uncertainties.unumpy import log10,log,exp,sqrt
from analysis.aux.units.uncertainties.unumpy import sin,cos,tan
from analysis.aux.units.uncertainties.unumpy import arcsin,arccos,arctan
#from ivs.sed import filters
#from ivs.io import ascii
#from ivs.aux import loggers
#from ivs.aux.decorators import memoized

#logger = logging.getLogger("UNITS.CONV")
#logger.addHandler(loggers.NullHandler())

#{ Main functions

def convert(_from,_to,*args,**kwargs):
    
    #-- remember if user wants to unpack the results to have no trace of
    #   uncertainties, or wants to get uncertainty objects back
    unpack = kwargs.pop('unpack',True)
    
    #-- get the input arguments: if only one is given, it is either an
    #   C{uncertainty} from the C{uncertainties} package, or it is just a float
    if len(args)==1:
        start_value = args[0]
    #   if two arguments are given, we assume the first is the actual value and
    #   the second is the error on the value
    elif len(args)==2:
        start_value = unumpy.uarray([args[0],args[1]])
    else:
        raise ValueError('illegal input')
    
    #-- (un)logarithmicize (denoted by '[]')
    m_in = re.search(r'\[(.*)\]',_from)
    m_out = re.search(r'\[(.*)\]',_to)
    
    if m_in is not None:
        _from = m_in.group(1)
        start_value = 10**start_value
    if m_out is not None:
        _to = m_out.group(1)
        
    #-- It is possible the user gave a convention for either the from or to
    #   units (but not both!)
    #-- break down the from and to units to their basic elements
    if _from in _conventions:
        _from = change_convention(_from,_to)
    elif _to in _conventions:
        _to = change_convention(_to,_from)
    fac_from,uni_from = breakdown(_from)
    fac_to,uni_to = breakdown(_to)
    
    #-- convert the kwargs to SI units if they are tuples (make a distinction
    #   when uncertainties are given)
    if uni_from!=uni_to and is_basic_unit(uni_from,'length') and not ('wave' in kwargs):# or 'freq' in kwargs_SI or 'photband' in kwargs_SI):
        kwargs['wave'] = (start_value,_from)
        #logger.warning('Assumed input value to serve also for "wave" key')
    elif uni_from!=uni_to and is_type(uni_from,'frequency') and not ('freq' in kwargs):# or 'freq' in kwargs_SI or 'photband' in kwargs_SI):
        kwargs['freq'] = (start_value,_from)
        #logger.warning('Assumed input value to serve also for "freq" key')
    kwargs_SI = {}
    for key in kwargs:
        if isinstance(kwargs[key],tuple):
            kwargs_SI[key] = convert(kwargs[key][-1],'SI',*kwargs[key][:-1],unpack=False)
        else:
            kwargs_SI[key] = kwargs[key]
    #-- add some default values if necessary
    #logger.debug('Convert %s to %s, fac_from-start_value %s/%s'%(uni_from,uni_to,fac_from,start_value))
    
    #-- conversion is easy if same units
    ret_value = 1.
    
    if uni_from==uni_to:
        #-- if nonlinear conversions from or to:
        if isinstance(fac_from,NonLinearConverter):
            ret_value *= fac_from(start_value,**kwargs_SI)
        else:
            try:
                ret_value *= fac_from*start_value
            except TypeError:
                raise TypeError('Cannot multiply value with a float; probably argument is a tuple (value,error), please expand with *(value,error)')
    
    #-- otherwise a little bit more complicated
    else:
        #-- first check where the unit differences are
        uni_from_ = uni_from.split()
        uni_to_ = uni_to.split()
        only_from_c,only_to_c = sorted(list(set(uni_from_) - set(uni_to_))),sorted(list(set(uni_to_) - set(uni_from_)))
        only_from_c,only_to_c = [list(components(i))[1:] for i in only_from_c],[list(components(i))[1:] for i in only_to_c]
        #-- push them all bach to the left side (change sign of right hand side components)
        left_over = " ".join(['%s%d'%(i,j) for i,j in only_from_c])
        left_over+= " "+" ".join(['%s%d'%(i,-j) for i,j in only_to_c])
        left_over = breakdown(left_over)[1]
        #-- but be sure to convert everything to SI units so that the switch
        #   can be interpreted.
        left_over = [change_convention('SI',ilo) for ilo in left_over.split()]
        only_from = "".join(left_over)
        only_to = ''

        #-- first we remove any differences concerning (ster)radians
        #   we recently added fac_from* to all these things, maybe this needs to 
        #   change?
        #if 'rad2' in only_from:
        #    start_value = fac_from*_switch['rad2_to_'](start_value,**kwargs_SI)
        #    only_from = only_from.replace('rad2','')
        #    logger.debug('Switching to /sr')
        #    fac_from = 1.
        #elif 'rad-2' in only_from:
        #    start_value = fac_from*_switch['rad-2_to_'](start_value,**kwargs_SI)
        #    only_from = only_from.replace('rad-2','')
        #    logger.debug('Switching from /sr')
        #    fac_from = 1.
        #elif 'rad1' in only_from:
        #    start_value = fac_from*_switch['rad1_to_'](start_value,**kwargs_SI)
        #    only_from = only_from.replace('rad1','')
        #    logger.debug('Switching to /rad')
        #    fac_from = 1.
        #elif 'rad-1' in only_from:
        #    start_value = fac_from*_switch['rad-1_to_'](start_value,**kwargs_SI)
        #    only_from = only_from.replace('rad-1','')
        #    logger.debug('Switching from /rad')
        #    fac_from = 1.
        
        #-- then we do what is left over (if anything is left over)
        if only_from or only_to:
            #logger.debug("Convert %s to %s"%(only_from,only_to))
            
            #-- nonlinear conversions need a little tweak
            try:
                key = '%s_to_%s'%(only_from,only_to)
                #logger.debug('Switching from {} to {} via {:s}'.format(only_from,only_to,_switch[key].__name__))
                if isinstance(fac_from,NonLinearConverter):
                    ret_value *= _switch[key](fac_from(start_value,**kwargs_SI),**kwargs_SI)
                #-- linear conversions are easy
                else:
                    #logger.debug('fac_from=%s, start_value=%s with kwargs %s'%(fac_from,start_value,kwargs_SI))
                    ret_value *= _switch[key](fac_from*start_value,**kwargs_SI)
            except KeyError:
                #-- try to be smart an reverse the units:
                if not (Unit(1.,uni_from)*Unit(1.,uni_to))[1]:
                    ret_value *= period2freq(fac_from*start_value,**kwargs_SI)
                    #logger.warning('It is assumed that the "from" unit is the inverse of the "to" unit')
                else:
                    #logger.critical('cannot convert %s to %s: no %s definition in dict _switch'%(_from,_to,key))
                    raise
        else:
            ret_value *= start_value
    #-- final step: convert to ... (again distinction between linear and
    #   nonlinear converters)
    if isinstance(fac_to,NonLinearConverter):
        ret_value = fac_to(ret_value,inv=True,**kwargs_SI)
    else:
        ret_value /= fac_to
    
    #-- logarithmicize
    if m_out is not None:
        ret_value = log10(ret_value)
        
    #-- unpack the uncertainties if: 
    #    1. the input was not given as an uncertainty
    #    2. the input was without uncertainties, but extra keywords had uncertainties
    #    3. the input was with uncertainties (float or array) and unpack==True
    unpack_case1 = len(args)==2
    unpack_case2 = len(args)==1 and isinstance(ret_value,AffineScalarFunc)
    unpack_case3 = len(args)==1 and isinstance(ret_value,np.ndarray) and isinstance(ret_value[0],AffineScalarFunc)
    if unpack and (unpack_case1 or unpack_case2):
        ret_value = unumpy.nominal_values(ret_value),unumpy.std_devs(ret_value)
        #-- convert to real floats if real floats were given
        if not ret_value[0].shape:
            ret_value = np.asscalar(ret_value[0]),np.asscalar(ret_value[1])    
    
    return ret_value


def nconvert(_froms,_tos,*args,**kwargs):
    """
    Convert a list/array/tuple of values with different units to other units.
    
    This silently catches some exceptions and replaces the value with nan!
    
    @rtype: array
    """
    if len(args)==1:
        ret_value = np.zeros((len(args[0])))
    elif len(args)==2:
        ret_value = np.zeros((len(args[0]),2))
    if isinstance(_tos,str):
        _tos = [_tos for i in _froms]
    elif isinstance(_froms,str):
        _froms = [_froms for i in _tos]
    
    for i,(_from,_to) in enumerate(list(zip(_froms,_tos))):
        myargs = [iarg[i] for iarg in args]
        mykwargs = {}
        for key in kwargs:
            if not isinstance(kwargs[key],str) and hasattr(kwargs[key],'__iter__'):
                mykwargs[key] = kwargs[key][i]
            else:
                mykwargs[key] = kwargs[key]
        try:
            ret_value[i] = convert(_from,_to,*myargs,**mykwargs)
        except ValueError: #no calibration
            ret_value[i] = np.nan
    
    if len(args)==2:
        ret_value = ret_value.T
    return ret_value


def change_convention(to_,units,origin=None):
    """
    Change units from one convention to another.
        
    Example usage:
    
    >>> units1 = 'kg m2 s-2 K-1 mol-1'
    >>> print change_convention('cgs',units1)
    K-1 g1 cm2 mol-1 s-2
    >>> print change_convention('sol','g cm2 s-2 K-1 mol-1')
    Tsol-1 Msol1 Rsol2 mol-1 s-2
    
    @param to_: convention name to change to
    @type to_: str
    @param units: units to change
    @type units: str
    @param origin: the name of the original convention
    @type origin: str
    @return: units in new convention
    @rtype: str
    """
    if origin is None:
        origin = constants._current_convention
    #-- break down units in base units, and breakdown that string in
    #   whole units and non-alpha digits (we'll weave them back in after
    #   translation)
    factor,units = breakdown(units)
    new_units = re.findall(r'[a-z]+',units, re.I)
    powers = re.findall(r'[0-9\W]+',units, re.I)
    #-- make the translation dictionary
    translator = {}
    for key in sorted(_conventions[origin].keys()):
        translator[_conventions[origin][key]] = _conventions[to_][key]
    #-- translate
    new_units = [unit in translator and translator[unit] or unit for unit in new_units]
    #-- weave them back in
    new_units = "".join(["".join([i,j]) for i,j in zip(new_units,powers)])
    return new_units
    
def set_convention(units='SI',values='standard',frequency='rad'):
    """
    Consistently change the values of the fundamental constants to the paradigm
    of other system units or programs.
    
    This can be important in pulsation codes, where e.g. the value of the
    gravitational constant and the value of the solar radius can have siginficant
    influences on the frequencies.
    
    Setting C{frequency} to C{rad} gives you the following behaviour:
    
    >>> old_settings = set_convention(frequency='rad')
    >>> convert('s-1','rad/s',1.0)
    1.0
    >>> convert('cy/s','rad/s',1.0)
    6.283185307179586
    >>> convert('Hz','rad/s',1.0)
    6.283185307179586
    >>> convert('','mas',constants.au/(1000*constants.pc))
    0.99999999999623
    
    Setting C{frequency} to C{cy} gives you the following behaviour:
    
    >>> old_settings = set_convention(frequency='cy')
    >>> convert('s-1','rad/s',1.0)
    6.283185307179586
    >>> convert('cy/s','rad/s',1.0)
    6.283185307179586
    >>> convert('Hz','rad/s',1.0)
    6.283185307179586
    >>> convert('','mas',constants.au/(1000*constants.pc))
    6.2831853071558985
    >>> old_settings = set_convention(*old_settings)
    
    @param units: name of the new units base convention
    @type units: str
    @param values: name of the value set for fundamental constants
    @type values: str
    @return: name of old convention
    @rtype: str
    """
    to_return = constants._current_convention,\
                constants._current_values,\
                constants._current_frequency
    values = values.lower()
    if to_return==(units,values,frequency):
        #logger.info('No need to change convention or values')
        return to_return
    if to_return[2]!=frequency and 'cy' in frequency.lower():
        _switch['rad1_to_'] = per_cy
        _switch['rad-1_to_'] = times_cy
        constants._current_frequency = frequency.lower()
        #logger.debug('Changed frequency convention to {0}'.format(frequency))
    elif to_return[2]!=frequency and 'rad' in frequency.lower():
        _switch['rad1_to_'] =  do_nothing
        _switch['rad-1_to_'] = do_nothing
        constants._current_frequency = frequency.lower()
        #logger.debug('Changed frequency convention to {0}'.format(frequency))
        
    if to_return[:2]==(units,values):
        return to_return
    #-- first reload the constants to their original values (i.e. SI based)
    #reload(constants)
    #-- then, where possible, replace all the constants with the value from
    #   the other convention:
    cvars = dir(constants)
    cvars = [i for i in cvars if i+'_units' in cvars]
    for const in cvars:
        #-- break down the old units in their basic components, but replace
        #   the value of the constant from those of the C{values} set if
        #   possible
        old_units = getattr(constants,const+'_units')
        const_ = const+'_'+values
        if hasattr(constants,const_):
            #logger.info('Override {0} value to {1}'.format(const,values))
            old_value = getattr(constants,const_)
            #-- remember, the old values is always in SI
            if not to_return[1]=='SI':
                SI_units = change_convention('SI',old_units)
                old_value = convert(SI_units,old_units,old_value)
        else:
            old_value = getattr(constants,const)
        factor,old_units = breakdown(old_units)
        #-- replace old base units with new conventions's base units
        new_units = change_convention(units,old_units)
        #-- convert the value from the old to the new convenction
        new_value = convert(old_units,new_units,old_value)
        #-- and attach to the constants module
        setattr(constants,const,new_value)
        setattr(constants,const+'_units',new_units)
    #-- convert the _factors in this module to the new convention. First make
    #   new list of factors, only to later overwrite the original one as a whole.
    #   We do this because e.g. while converting everything to yd, we would
    #   overwrite the definition of 'm' with that of 'yd', so that we would lose
    #   the conversion information on the fly
    new_factors = {}
    for fac in _factors:
        _from = _factors[fac][1]
        #-- some quantities have no units.
        if not _from:
            new_factors[fac] = _factors[fac]
            continue
        _to = change_convention(units,_from)
        #-- if this unit is one of the base units of any system, make sure not
        #   to set the '1' at the end, or we'll get in trouble...
        try:
            for conv in _conventions:
                for typ in _conventions[conv]:
                    if _conventions[conv][typ]==_to[:-1] and _to[-1]=='1':
                        _to = _to[:-1]
                        raise StopIteration
        except StopIteration:
            pass
        if _from==_to[:-1] and _to[-1]=='1':
            _to = _to[:-1]
        #-- some stuff cannot be converted
        try:
            new_factors[fac] = (convert(_from,_to,_factors[fac][0]),_to,_factors[fac][2],_factors[fac][3])
        except ValueError:
            new_factors[fac] = _factors[fac]
            continue
        except TypeError:
            new_factors[fac] = _factors[fac]
            continue
    for fac in _factors:
        _factors[fac] = new_factors[fac]
    #-- convert the names of combinations of basic units:
    for name in list(_names.keys()):
        new_name = change_convention(units,name)
        _names[new_name] = _names.pop(name)
    #-- convert the switches in this module to the new convention
    #for switch in _switch:
        
    constants._current_convention = units
    constants._current_values = values
    #-- when we set everything back to SI, make sure we have no rounding errors:
    if units=='SI' and values=='standard' and frequency=='rad':
        reload(constants)
        #logger.warning('Reloading of constants')
    #logger.info('Changed convention to {0} with values from {1} set'.format(units,values))
    return to_return

def reset():
    """
    Resets all values and conventions to the original values.
    """
    set_convention()

def get_convention():
    """
    Returns convention and name of set of values.
    
    >>> get_convention()
    ('SI', 'standard', 'rad')
    
    @rtype: str,str
    @return: convention, values
    """
    return constants._current_convention,constants._current_values,constants._current_frequency
            

def get_constant(constant_name,units='SI',value='standard'):
    """
    Convenience function to retrieve the value of a constant in a particular
    system.
    
    >>> Msol = get_constant('Msol','SI')
    >>> Msol = get_constant('Msol','kg')
    
    @param constant_name: name of the constant
    @type constant_name: str
    @param units: name of the unit base system
    @type units: str
    @param value: name of the parameter set the get the value from
    @type value: str
    @return: value of the constant in the unit base system
    @rtype: float
    """
    value = value.lower()
    #-- see if we need (and have) a different value than the standard one
    const_ = constant_name+'_'+value
    old_units = getattr(constants,constant_name+'_units')
    if hasattr(constants,const_):
        old_value = getattr(constants,const_)
        old_units = change_convention('SI',old_units)
    else:
        old_value = getattr(constants,constant_name)
    new_value = convert(old_units,units,old_value)
    return new_value

def get_constants(units='SI',values='standard'):
    """
    Convenience function to retrieve the value of all constants in a particular
    system.
    
    This can be helpful when you want to attach a copy of the fundamental constants
    to some module or class instances, and be B{sure} these values never change.
    
    Yes, I know, there's a lot that can go wrong with values that are supposed
    to be CONSTANT!
    
    @rtype: dict
    """
    cvars = dir(constants)
    cvars = [i for i in cvars if i+'_units' in cvars]
    myconstants = {}
    for cvar in cvars:
        myconstants[cvar] = get_constant(cvar,units,values)
    return myconstants
    
    
    
    

#}
#{ Conversions basics and helper functions


def solve_aliases(unit):
    """
    Resolve simple aliases in a unit's name.
    
    Resolves aliases and replaces division signs with negative power.
    
    @param unit: unit (e.g. erg s-1 angstrom-1)
    @type unit: str
    @return: aliases-resolved unit (e.g. erg s-1 AA-1)
    @rtype: str
    """
    #-- resolve aliases
    for alias in _aliases:
        unit = unit.replace(alias[0],alias[1])
    
    #-- replace slash-forward with negative powers
    if '/' in unit:
        unit_ = [uni.split('/') for uni in unit.split()]
        for i,uni in enumerate(unit_):
            for j,after_div in enumerate(uni[1:]):
                if not after_div[-1].isdigit(): after_div += '1'
                #m = re.search(r'(\d*)(.+?)(-{0,1}\d+)',after_div)
                m = re.search(r'(\d*)(.+?)(-{0,1}[\d\.]+)',after_div)
                if m is not None:
                    factor,basis,power = m.group(1),m.group(2),m.group(3)
                    if not '.' in power: power = int(power)
                    else:                power = float(power)
                    if factor: factor = float(factor)
                    else: factor = 1.
                else:
                    factor,basis,power = 1.,after_div,1
                uni[1+j] = '%s%g'%(basis,-power)
                if factor!=1: uni[1+j] = '%d%s'%(factor,uni[1+j])
        ravelled = []
        for uni in unit_:
            ravelled += uni
        unit = " ".join(ravelled)
    return unit
    

def components(unit):
    """
    Decompose a unit into: factor, SI base units, power.
    
    You probably want to call L{solve_aliases} first.
    
    Examples:
    
    >>> factor1,units1,power1 = components('m')
    >>> factor2,units2,power2 = components('g2')
    >>> factor3,units3,power3 = components('hg3')
    >>> factor4,units4,power4 = components('Mg4')
    >>> factor5,units5,power5 = components('mm')
    >>> factor6,units6,power6 = components('W3')
    >>> factor7,units7,power7 = components('s-2')
    
    >>> print "{0}, {1}, {2}".format(factor1,units1,power1)
    1.0, m, 1
    >>> print "{0}, {1}, {2}".format(factor2,units2,power2)
    0.001, kg, 2
    >>> print "{0}, {1}, {2}".format(factor3,units3,power3)
    0.1, kg, 3
    >>> print "{0}, {1}, {2}".format(factor4,units4,power4)
    1000.0, kg, 4
    >>> print "{0}, {1}, {2}".format(factor5,units5,power5)
    0.001, m, 1
    >>> print "{0}, {1}, {2}".format(factor6,units6,power6)
    1.0, m2 kg1 s-3, 3
    >>> print "{0}, {1}, {2}".format(factor7,units7,power7)
    1.0, s, -2
    
    @param unit: unit name
    @type unit: str
    @return: 3-tuple with factor, SI base unit and power
    @rtype: (float,str,int)
    """
    factor = 1.
    #-- manually check if there is a prefactor of the form '10-14' or '10e-14'
    #   and expand it if necessary
    m = re.search('\d\d[-+]\d\d',unit[:5])
    if m is not None:
        factor *= float(m.group(0)[:2])**(float(m.group(0)[2]+'1')*float(m.group(0)[3:5]))
        unit = unit[5:]
    m = re.search('\d\d[[eE][-+]\d\d',unit[:6])
    if m is not None:
        factor *= float(m.group(0)[:2])**(float(m.group(0)[3]+'1')*float(m.group(0)[4:6]))
        unit = unit[6:]
    
    if not unit[-1].isdigit(): unit += '1'
    #-- decompose unit in base name and power
    #m = re.search(r'(\d*)(.+?)(-{0,1}\d+)',unit)
    m = re.search(r'(\d*)(.+?)(-{0,1}[\d\.]+)',unit)
    if m is not None:
        factor_,basis,power = m.group(1),m.group(2),m.group(3)
        #-- try to make power an integer, otherwise make it a float
        if not '.' in power: power = int(power)
        else:                power = float(power)
        if factor_:
            factor *= float(factor_)
    else:
        factor,basis,power = 1.,unit,1
    #-- decompose the base name (which can be a composition of a prefix
    #   (e.g., 'mu') and a unit name (e.g. 'm')) into prefix and unit name
    #-- check if basis is part of _factors dictionary. If not, find the
    #   combination of _scalings and basis which is inside the dictionary!
    
    for scale in _scalings:
        scale_unit,base_unit = basis[:len(scale)],basis[len(scale):]
        if scale_unit==scale and base_unit in _factors and not basis in _factors:
            #if basis in _factors:
            #    raise ValueError,'ambiguity between %s and %s-%s'%(basis,scale_unit,base_unit)
            factor *= _scalings[scale]
            basis = base_unit
            break
    #-- if we didn't find any scalings, check if the 'raw' unit is already
    #   a base unit
    else:
        if not basis in _factors:
            raise ValueError('Unknown unit %s'%(basis))
        
    #-- switch from base units to SI units
    if hasattr(_factors[basis][0],'__call__'):
        factor = factor*_factors[basis][0]()
    else:
        factor *= _factors[basis][0]
    basis = _factors[basis][1]
    
    return factor,basis,power



def breakdown(unit):
    """
    Decompose a unit into SI base units containing powers.
    
    Examples:
    
    >>> factor1,units1 = breakdown('erg s-1 W2 kg2 cm-2')
    >>> factor2,units2 = breakdown('erg s-1 cm-2 AA-1')
    >>> factor3,units3 = breakdown('W m-3')
    
    
    >>> print "{0}, {1}".format(factor1,units1)
    0.001, kg5 m4 s-9
    >>> print "{0}, {1}".format(factor2,units2)
    10000000.0, kg1 m-1 s-3
    >>> print "{0}, {1}".format(factor3,units3)
    1.0, kg1 m-1 s-3
    
    @param unit: unit's name
    @type unit: str
    @return: 2-tuple factor, unit's base name
    @rtype: (float,str)
    """
    #-- solve aliases
    unit = solve_aliases(unit)
    #-- break down in basic units
    units = unit.split()
    total_factor = 1.
    total_units = []
    total_power = []
    for unit in units:
        factor,basis,power = components(unit)
        total_factor = total_factor*factor**power
        basis = basis.split()
        for base in basis:
            factor_,basis_,power_ = components(base)
            try:
                total_factor = total_factor*factor_
            except TypeError:
                pass
            if basis_ in total_units:
                index = total_units.index(basis_)
                total_power[index] += power_*power
            else:
                total_units.append(basis_)
                total_power.append(power_*power)
    
    #-- make sure to return a sorted version
    total_units = sorted(['%s%s'%(i,j) for i,j in zip(total_units,total_power) if j!=0])
    return total_factor," ".join(total_units)

def compress(unit,ignore_factor=False):
    """
    Compress basic units to more human-readable type.
    
    If you are only interested in a shorter form of the units, regardless
    of the values, you need to set C{ignore_factor=True}.
    
    For example: erg/s/cm2/AA can be written shorter as W1 m-3, but 1 erg/s/cm2/AA
    is not equal to 1 W1 m-3. Thus:
    
    >>> print(compress('erg/s/cm2/AA',ignore_factor=True))
    W1 m-3
    >>> print(compress('erg/s/cm2/AA',ignore_factor=False))
    erg/s/cm2/AA
    
    You cannot write the latter shorter without converting the units!
    
    For the same reasoning, compressing non-basic units will not work out:
    
    >>> print(compress('Lsol'))
    Lsol
    >>> print(compress('Lsol',ignore_factor=True))
    W1 
    
    So actually, this function is really designed to compress a combination
    of basic units:
    
    >>> print(compress('kg1 m-1 s-3'))
    W1 m-3
        
    @param unit: combination of units to compress
    @type unit: str
    @param ignore_factor: if True, then a `sloppy compress' will be performed,
    that is, it is not guaranteed that the multiplication factor equals 1 (see
    examples)
    @type ignore_factor: bool
    @rtype: str
    @return: shorter version of original units
    
    """
    #-- if input is not a basic unit (or is not trivially expandable as
    #   a basic unit, do nothing)
    breakdown_units = breakdown(unit)
    if not ignore_factor and breakdown_units[0]!=1.:
        return unit
    #-- else, look for ways to simplify it
    orig_units = Unit(1.,breakdown_units[1])
    for fac in _factors:
        try:
            if not np.allclose(_factors[fac][0],1.): continue
            y = Unit(1.,fac+'1')
            left_over = (orig_units/y)
            value,left_over = left_over[0],left_over[1]
        except:
            continue
        new_unit = ' '.join([y[1],left_over])
        if len(new_unit)<len(orig_units[1]):
            orig_units = Unit(1.,new_unit)
    
    return orig_units[1]

def split_units_powers(unit):
    """
    Split unit in a list of units and a list of powers.
    
    >>> y = 'AA A kg-2 F-11 Wb-1'
    >>> split_units_powers(y)
    (['AA', 'A', 'kg', 'F', 'Wb'], ['1', '1', '-2', '-11', '-1'])
    >>> y = 'angstrom A1/kg2 F-11/Wb'
    >>> split_units_powers(y)
    (['AA', 'A', 'kg', 'F', 'Wb'], ['1', '1', '-2', '-11', '-1'])
    >>> y = '10angstrom A1/kg2 F-11/Wb'
    >>> split_units_powers(y)
    (['10AA', 'A', 'kg', 'F', 'Wb'], ['1', '1', '-2', '-11', '-1'])
    >>> y = '10angstrom A1/kg2 5F-11/Wb'
    >>> split_units_powers(y)
    (['10AA', 'A', 'kg', '5F', 'Wb'], ['1', '1', '-2', '-11', '-1'])
    >>> y = '10angstrom A0.5/kg2 5F-11/100Wb2.5'
    >>> split_units_powers(y)
    (['10AA', 'A', 'kg', '5F', '100Wb'], ['1', '0.5', '-2', '-11', '-2.5'])
    
    @param unit: unit
    @type unit: str
    @rtype: list,list
    @return: list of unit names, list of powers
    """
    #-- compile regular expressions
    units  = re.compile('\A[\d].[a-z]+|[a-z]+|\s\d+[a-z]+',re.IGNORECASE)
    powers = re.compile('[-\.\d]+[\s]|[-\.\d]+\Z')
    #-- solve aliases
    unit = solve_aliases(unit)
    #-- and make sure every unit has at least one power:
    unit = ' '.join([comp[-1].isdigit() and comp or comp+'1' for comp in unit.split()])
    #-- now split and remove spaces
    units  = [i.strip() for i in units.findall(unit)]
    powers = [i.strip() for i in powers.findall(unit)] 
    return units,powers
    

def is_basic_unit(unit,type):
    """
    Check if a unit is represents one of the basic quantities (length, mass...)
    
    >>> is_basic_unit('K','temperature')
    True
    >>> is_basic_unit('m','length')
    True
    >>> is_basic_unit('cm','length')
    True
    >>> is_basic_unit('km','length') # not a basic unit in any type of convention
    False
    
    @parameter unit: unit to check
    @type unit: str
    @parameter type: type to check
    @type type: str
    @rtype: bool
    """
    for conv in _conventions:
        if unit==_conventions[conv][type] or (unit[:-1]==_conventions[conv][type] and unit[-1]=='1'):
            return True
    else:
        return False

def is_type(unit,type):
    """
    Check if a unit is of a certain type (mass, length, force...)
    
    >>> is_type('K','temperature')
    True
    >>> is_type('m','length')
    True
    >>> is_type('cm','length')
    True
    >>> is_type('ly','length')
    True
    >>> is_type('ly','time')
    False
    >>> is_type('W','power')
    True
    >>> is_type('kg m2/s3','power')
    True
    >>> is_type('kg/s2/m','pressure')
    True
    >>> is_type('W','force')
    False
    
    @parameter unit: unit to check
    @type unit: str
    @parameter type: type to check
    @type type: str
    @rtype: bool
    """
    #-- solve aliases and breakdown in basic SI units
    unit = solve_aliases(unit)
    comps = breakdown(unit)[1]
    for fac in _factors:
        this_unit = change_convention(constants._current_convention,_factors[fac][1])
        if comps==this_unit and type in _factors[fac][2].split('/'):
            return True
    #-- via names
    if comps in _names and _names[comps]==type:
        return True
        
    return False

def get_type(unit):
    """
    Return human readable name of the unit
    
    @rtype: str
    """
    comps = breakdown(unit)[1]
    if comps in _names:
        return _names[comps]
    for i in _factors:
        test_against = breakdown(i)[1]
        if test_against==comps or unit==test_against:
            return _factors[i][2]
    return unit

def round_arbitrary(x, base=5):
    """
    Round to an arbitrary base.
    
    Example usage:
    
    >>> round_arbitrary(1.24,0.25)
    1.25
    >>> round_arbitrary(1.37,0.75)
    1.5
    
    
    @param x: number to round
    @type x: float
    @param base: base to round to
    @type base: integer or float
    @return: rounded number
    @rtype: float
    """
    return base * round(float(x)/base)

def unit2texlabel(unit,full=False):
    """
    Convert a unit string to a nicely formatted TeX label.
    
    For fluxes, also the Flambda, lambda Flambda, or Fnu, or nuFnu is returned.
    
    @param unit: unit name
    @type unit: str
    @param full: return "name [unit]" or only "unit"
    @type full: bool
    @rtype: str
    """
    unit = solve_aliases(unit)
    fac,base = breakdown(unit)
    names,powers = split_units_powers(unit)
    powers = [(power!='1' and power or ' ') for power in powers]
    powers = [(power[0]!=' ' and '$^{{{0}}}$'.format(power) or power) for power in powers]
    unit_ = ' '.join([(name+power) for name,power in zip(names,powers)])
    
    translate = {'kg1 m-1 s-3' :r'$F_\lambda$ [{0}]'.format(unit_),
                 'cy-1 kg1 s-2':r'$F_\nu$ [{0}]'.format(unit_),
                 'kg1 s-3':r'$\lambda F_\lambda$ [{0}]'.format(unit_),
                }
    #translate = {}
    #-- translate
    if base in translate:
        label = translate[base]
    else:
        label = unit_
    #-- make TeX
    label = label.replace('AA','$\AA$')
    label = label.replace('mu','$\mu$')
    label = label.replace('sol','$_\odot$')
    
    if full:
        label = '{0} [{1}]'.format(get_type(unit).title(),label.strip())
    
    return label        
    




def get_help():
    """
    Return a string with a list and explanation of all defined units.
    
    @rtype: str
    """
    try:
        set_exchange_rates()
    except IOError:
       pass
        #logger.warning('Unable to connect to ecb.europa.eu')
    help_text = {}
    for fac in sorted(_factors.keys()):
        if _factors[fac][2] not in help_text:
            help_text[_factors[fac][2]] = []
        help_text[_factors[fac][2]].append('%-15s = %-30s'%(fac,_factors[fac][3]))
    text = [[],[]]
    bar = '%-48s'%('================='+20*'=')
    for i,key in enumerate(sorted(help_text.keys())):
        text[i%2] += [bar,'%-48s'%("=   Units of %-20s   ="%(key)),bar]
        text[i%2] += help_text[key]
    out = ''
    #for i,j in itertools.zip_longest(*text,fillvalue=''): # for Python 3
    for i,j in itertools.izip_longest(*text,fillvalue=''):
        out += '%s| %s\n'%(i,j)
    
    return out

def units2sphinx():
    set_exchange_rates()
    text = []
    divider = "+{0:s}+{1:s}+{2:s}+{3:s}+{4:s}+".format(22*'-',52*'-',22*'-',32*'-',52*'-')
    text.append(divider)
    text.append("| {0:20s} | {1:50} | {2:20s} | {3:30s} | {4:50s} |".format('name','factor','units','type','description'))
    text.append(divider)
    for key in _factors:
        text.append("| {0:20s} | {1:50} | {2:20s} | {3:30s} | {4:50s} |".format(key,*_factors[key]))
        text.append(divider)
    return "\n".join(text)
    
def derive_wrapper(fctn):
    @functools.wraps(fctn)
    def func(*args,**kwargs):
        argspec = inspect.getargspec(fctn)
        print (argspec)
        result = fctn(*args,**kwargs)
        return result
    return func
#}
#{ Linear change-of-base conversions
        
def distance2velocity(arg,**kwargs):
    """
    Switch from distance to velocity via a reference wavelength.
    
    @param arg: distance (SI, m)
    @type arg: float
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @return: velocity (SI, m/s)
    @rtype: float
    """
    if 'wave' in kwargs:
        wave = kwargs['wave']
        velocity = (arg-wave) / wave * constants.cc
    else:
        raise ValueError('reference wavelength (wave) not given')
    return velocity

def velocity2distance(arg,**kwargs):
    """
    Switch from velocity to distance via a reference wavelength.
    
    @param arg: velocity (SI, m/s)
    @type arg: float
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @return: distance (SI, m)
    @rtype: float
    """
    if 'wave' in kwargs:
        wave = kwargs['wave']
        distance = wave / constants.cc * arg + wave
    else:
        raise ValueError('reference wavelength (wave) not given')
    return distance

def fnu2flambda(arg,**kwargs):
    """
    Switch from Fnu to Flambda via a reference wavelength.
    
    Flambda and Fnu are spectral irradiance in wavelength and frequency,
    respectively
    
    @param arg: spectral irradiance (SI,W/m2/Hz)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spectral irradiance (SI, W/m2/m)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        wave = kwargs['wave']
        flambda = constants.cc/wave**2 * arg
    elif 'freq' in kwargs:
        freq = kwargs['freq']/(2*np.pi)
        flambda = freq**2/constants.cc * arg
    else:
        raise ValueError('reference wave/freq not given')
    #if constants._current_frequency=='rad':
    return flambda*2*np.pi
    #else:
    #    return flambda

def flambda2fnu(arg,**kwargs):
    """
    Switch from Flambda to Fnu via a reference wavelength.
    
    Flambda and Fnu are spectral irradiance in wavelength and frequency,
    respectively
    
    @param arg: spectral irradiance (SI, W/m2/m)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spectral irradiance (SI,W/m2/Hz)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        wave = kwargs['wave']
        fnu = wave**2/constants.cc * arg
    elif 'freq' in kwargs:
        freq = kwargs['freq']/(2*np.pi)
        fnu = constants.cc/freq**2 * arg
    else:
        raise ValueError('reference wave/freq not given')
    #if constants._current_frequency=='rad':
    return fnu/(2*np.pi)
    #else:
    #    return fnu

def fnu2nufnu(arg,**kwargs):
    """
    Switch from Fnu to nuFnu via a reference wavelength.
    
    Flambda and Fnu are spectral irradiance in wavelength and frequency,
    respectively
    
    @param arg: spectral irradiance (SI,W/m2/Hz)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spectral irradiance (SI, W/m2/m)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        wave = kwargs['wave']
        fnu = constants.cc/wave * arg
    elif 'freq' in kwargs:
        freq = kwargs['freq']
        fnu = freq * arg
    else:
        raise ValueError('reference wave/freq not given')
    return fnu*(2*np.pi)

def nufnu2fnu(arg,**kwargs):
    """
    Switch from nuFnu to Fnu via a reference wavelength.
    
    Flambda and Fnu are spectral irradiance in wavelength and frequency,
    respectively
    
    @param arg: spectral irradiance (SI,W/m2/Hz)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spectral irradiance (SI, W/m2/m)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        wave = kwargs['wave']
        fnu = wave/constants.cc * arg
    elif 'freq' in kwargs:
        freq = kwargs['freq']
        fnu = arg / freq
    else:
        raise ValueError('reference wave/freq not given')
    return fnu/(2*np.pi)

def flam2lamflam(arg,**kwargs):
    """
    Switch from lamFlam to Flam via a reference wavelength.
    
    Flambda and Fnu are spectral irradiance in wavelength and frequency,
    respectively
    
    @param arg: spectral irradiance (SI,W/m2/Hz)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spectral irradiance (SI, W/m2/m)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        wave = kwargs['wave']
        lamflam = wave * arg
    elif 'freq' in kwargs:
        freq = kwargs['freq']
        lamflam = constants.cc/freq * arg
    else:
        raise ValueError('reference wave/freq not given')
    return lamflam

def lamflam2flam(arg,**kwargs):
    """
    Switch from lamFlam to Flam via a reference wavelength.
    
    Flambda and Fnu are spectral irradiance in wavelength and frequency,
    respectively
    
    @param arg: spectral irradiance (SI,W/m2/Hz)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spectral irradiance (SI, W/m2/m)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        wave = kwargs['wave']
        flam = arg / wave
    elif 'freq' in kwargs:
        freq = kwargs['freq']
        flam = arg / (cc/freq)
    else:
        raise ValueError('reference wave/freq not given')
    return flam

def distance2spatialfreq(arg,**kwargs):
    """
    Switch from distance to spatial frequency via a reference wavelength.
    
    @param arg: distance (SI, m)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: spatial frequency (SI, cy/as)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        spatfreq = 2*np.pi*arg/kwargs['wave']
    elif 'freq' in kwargs:
        spatfreq = 2*np.pi*arg/(constants.cc/kwargs['freq'])
    else:
        raise ValueError('reference wave/freq not given')
    return spatfreq

def spatialfreq2distance(arg,**kwargs):
    """
    Switch from spatial frequency to distance via a reference wavelength.
    
    @param arg: spatial frequency (SI, cy/as)
    @type arg: float
    @keyword photband: photometric passband
    @type photband: str ('SYSTEM.FILTER')
    @keyword wave: reference wavelength (SI, m)
    @type wave: float
    @keyword freq: reference frequency (SI, Hz)
    @type freq: float
    @return: distance (SI, m)
    @rtype: float
    """
    if 'photband' in kwargs:
        lameff = filters.eff_wave(kwargs['photband'])
        lameff = convert('AA','m',lameff)
        kwargs['wave'] = lameff
    if 'wave' in kwargs:
        distance = kwargs['wave']*arg/(2*np.pi)
    elif 'freq' in kwargs:
        distance = constants.cc/kwargs['freq']*arg/(2*np.pi)
    else:
        raise ValueError('reference wave/freq not given')
    return distance

def per_sr(arg,**kwargs):
    """
    Switch from [Q] to [Q]/sr
    
    @param arg: some SI unit
    @type arg: float
    @return: some SI unit per steradian
    @rtype: float
    """
    if 'ang_diam' in kwargs:
        radius = kwargs['ang_diam']/2.
        surface = np.pi*radius**2
    elif 'radius' in kwargs:
        radius = kwargs['radius']
        surface = np.pi*radius**2
    elif 'pix' in kwargs:
        pix = kwargs['pix']
        surface = pix**2
    else:
        raise ValueError('angular size (ang_diam/radius) not given')
    Qsr = arg/surface
    return Qsr

def times_sr(arg,**kwargs):
    """
    Switch from [Q]/sr to [Q]
    
    @param arg: some SI unit per steradian
    @type arg: float
    @return: some SI unit
    @rtype: float
    """
    if 'ang_diam' in kwargs:
        radius = kwargs['ang_diam']/2.
        surface = np.pi*radius**2
    elif 'radius' in kwargs:
        radius = kwargs['radius']
        surface = np.pi*radius**2
    elif 'pix' in kwargs:
        pix = kwargs['pix']
        surface = pix**2
    else:
        raise ValueError('angular size (ang_diam/radius) not given')
    Q = arg*surface
    return Q

def per_cy(arg,**kwargs):
    """
    Switch from radians/s to cycle/s
    
    @rtype: float
    """
    return arg / (2*np.pi)

def times_cy(arg,**kwargs):
    """
    Switch from cycle/s to radians/s
    
    @rtype: float
    """
    return 2*np.pi*arg

def period2freq(arg,**kwargs):
    """
    Convert period to frequency and back.
    
    @rtype: float
    """
    return 1./arg

    

def do_nothing(arg,**kwargs):
    #logger.warning('Experimental: probably just dropped the "cy" unit, please check results')
    return arg


def do_sqrt(arg,**kwargs):
    return sqrt(arg)

def do_quad(arg,**kwargs):
    return arg**2

def Hz2_to_ss(change,frequency):
    """
    Convert Frequency shift in Hz2 to period change in seconds per second.
    
    Frequency in Hz!
    """
    #-- to second per second
    second_per_second = abs(1/frequency - 1/(frequency-change))
    return second_per_second

#}


#{ Stellar calibrations

def derive_luminosity(radius,temperature,units=None):
    """
    Convert radius and effective temperature to stellar luminosity.
    
    Units given to radius and temperature must be understandable by C{Unit}.
    
    Stellar luminosity is returned, by default in Solar units.
    
    I recommend to give the input as follows, since this is most clear also
    from a programmatical point of view:
    
    >>> print(derive_luminosity((1.,'Rsol'),(5777,'K'),units='erg/s'))
    3.83916979644e+33 erg/s
    
    The function is pretty smart, however: it knows what the output units
    should be, so you could ask to put them in the 'standard' units of some
    convention:
    
    >>> print(derive_luminosity((1.,'Rsol'),(5777,'K'),units='cgs'))
    3.83916979644e+33 g1 cm2 s-3
    
    If you give nothing, everything will be interpreted in the current
    convention's units:
    
    >>> print(derive_luminosity(7e8,5777.))
    3.88892117726e+26 W1 
    
    You are also free to give units (you can do this in a number of ways), and
    if you index the return value, you can get the value ([0], float or
    uncertainty) or unit ([1],string):
    
    >>> derive_luminosity((1.,'Rsol'),(1.,0.1,'Tsol'),units='Lsol')[0]
    1.0000048246799305+/-0.4000019298719723
    
    >>> derive_luminosity((1.,'Rsol'),((1.,0.1),'Tsol'),units='Lsol')[0]
    1.0000048246799305+/-0.4000019298719723
    
    Finally, you can simply work with Units:
    
    >>> radius = Unit(1.,'Rsol')
    >>> temperature = Unit(5777.,'K')
    >>> print(derive_luminosity(radius,temperature,units='foe/s'))
    3.83916979644e-18 foe/s
    
    Everything should be independent of the current convention, unless it
    needs to be:
    
    >>> old = set_convention('cgs')
    >>> derive_luminosity((1.,'Rsol'),(1.,0.1,'Tsol'),units='Lsol')[0]
    1.0000048246799305+/-0.4000019298719722
    >>> print(derive_luminosity(7e10,5777.))
    3.88892117726e+33 erg1 s-1
    >>> sol = set_convention('sol')
    >>> print(derive_luminosity(1.,1.))
    3.9982620567e-22 Msol1 Rsol2 s-3
    >>> print(derive_luminosity(1.,1.,units='Lsol'))
    1.00000482468 Lsol
    >>> old = set_convention(*old)
    
    @param radius: (radius(, error), units)
    @type radius: 2 or 3 tuple, Unit or float (current convention)
    @param temperature: (effective temperature(, error), units)
    @type temperature: 2 or 3 tuple, Unit or float (current convention)
    @return: luminosity
    @rtype: Unit
    """
    if units is None:
        units = constants._current_convention
    radius = Unit(radius,unit='length')
    temperature = Unit(temperature,unit='temperature')
    sigma = Unit('sigma')
    
    lumi = 4*np.pi*sigma*radius**2*temperature**4
    
    return lumi.convert(units)
    

def derive_radius(luminosity,temperature, units='m'):
    """
    Convert luminosity and effective temperature to stellar radius.
    
    Units given to luminosity and temperature must be understandable by C{convert}.
    
    Stellar radius is returned in SI units.
    
    Example usage:
    
    >>> print(derive_radius((3.9,'[Lsol]'),(3.72,'[K]'),units='Rsol'))
    108.091293736 Rsol
    >>> print(derive_radius(3.8e26,5777.,units='Rjup'))
    9.89759670363 Rjup
    
    @param luminosity: (Luminosity(, error), units)
    @type luminosity: 2 or 3 tuple
    @param temperature: (effective temperature(, error), units)
    @type temperature: 2 or 3 tuple, Unit or float (current convention)
    @return: radius
    @rtype: Unit
    """
    if units is None:
        units = constants._current_convention
    luminosity = Unit(luminosity,unit='power')
    temperature = Unit(temperature,unit='temperature')
    sigma = Unit('sigma')
    
    R = np.sqrt(luminosity / (temperature**4)/(4*np.pi*sigma))
    
    return R.convert(units)    

def derive_radius_slo(numax,Deltanu0,teff,unit='Rsol'):
    """
    Derive stellar radius from solar-like oscillations diagnostics.
    
    Large separation is frequency spacing between modes of different radial order.
    
    Small separation is spacing between modes of even and odd angular degree but
    same radial order.
    
    @param numax: (numax(, error), units)
    @type numax: 2 or 3 tuple
    @param Deltanu0: (large separation(, error), units)
    @type Deltanu0: 2 or 3 tuple
    @param teff: (effective temperature(, error), units)
    @type teff: 2 or 3 tuple
    @return: radius (and error) in whatever units
    @rtype: 1- or 2-tuple
    """
    numax_sol = convert(constants.numax_sol[-1],'mHz',*constants.numax_sol[:-1],unpack=False)
    Deltanu0_sol = convert(constants.Deltanu0_sol[-1],'muHz',*constants.Deltanu0_sol[:-1],unpack=False)
    #-- take care of temperature
    if len(teff)==3:
        teff = (unumpy.uarray([teff[0],teff[1]]),teff[2])
    teff = convert(teff[-1],'5777K',*teff[:-1],unpack=False)
    #-- take care of numax
    if len(numax)==3:
        numax = (unumpy.uarray([numax[0],numax[1]]),numax[2])
    numax = convert(numax[-1],'mHz',*numax[:-1],unpack=False)
    #-- take care of large separation
    if len(Deltanu0)==3:
        Deltanu0 = (unumpy.uarray([Deltanu0[0],Deltanu0[1]]),Deltanu0[2])
    Deltanu0 = convert(Deltanu0[-1],'muHz',*Deltanu0[:-1],unpack=False)
    R = sqrt(teff)/numax_sol * numax/Deltanu0**2 * Deltanu0_sol**2
    return convert('Rsol',unit,R)    
    
#@derive_wrapper
def derive_radius_rot(veq_sini,rot_period,incl=(90.,'deg'),unit='Rsol'):
    """
    Derive radius from rotation period and inclination angle.
    """
    incl = Unit(incl)
    radius = veq_sini*rot_period/(2*np.pi)/np.sin(incl)
    return radius
    

    
    
def derive_logg(mass,radius, unit='[cm/s2]'):
    """
    Convert mass and radius to stellar surface gravity.
    
    Units given to mass and radius must be understandable by C{convert}.
    
    Logarithm of surface gravity is returned in CGS units.
    
    @param mass: (mass(, error), units)
    @type mass: 2 or 3 tuple
    @param radius: (radius(, error), units)
    @type radius: 2 or 3 tuple
    @return: log g (and error) in CGS units
    @rtype: 1- or 2-tuple
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'g',*mass[:-1],unpack=False)
    #-- take care of radius
    if len(radius)==3:
        radius = (unumpy.uarray([radius[0],radius[1]]),radius[2])
    R = convert(radius[-1],'cm',*radius[:-1],unpack=False)
    #-- calculate surface gravity in logarithmic CGS units
    logg = log10(constants.GG_cgs*M / (R**2))
    logg = convert('[cm/s2]',unit,logg)
    return logg
    
    
def derive_logg_zg(mass, zg, unit='cm s-2', **kwargs):
    """
    Convert mass and gravitational redshift to stellar surface gravity. Provide the gravitational
    redshift as a velocity.
    
    Units given to mass and zg must be understandable by C{convert}.
    
    Logarithm of surface gravity is returned in CGS units unless otherwhise stated in unit.
    
    >>> print derive_logg_zg((0.47, 'Msol'), (2.57, 'km/s'))
    5.97849814386
    
    @param mass: (mass(, error), units)
    @type mass: 2 or 3 tuple
    @param zg: (zg(, error), units)
    @type zg: 2 or 3 tuple
    @param unit: unit of logg
    @type unit: str
    @return: log g (and error)
    @rtype: 1- or 2-tuple
    """
    
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'g',*mass[:-1],unpack=False)
    #-- take care of zg
    if len(zg)==3:
        zg = (unumpy.uarray([zg[0],zg[1]]),zg[2])
    gr = convert(zg[-1],'cm s-1',*zg[:-1],unpack=False, **kwargs)
    
    logg = np.log10( gr**2 * constants.cc_cgs**2 / (constants.GG_cgs * M) )
    return convert('cm s-2', unit, logg)

def derive_logg_slo(teff,numax, unit='[cm/s2]'):
    """
    Derive stellar surface gravity from solar-like oscillations diagnostics.
    
    Units given to teff and numax must be understandable by C{convert}.
    
    Logarithm of surface gravity is returned in CGS units.
    
    @param teff: (effective temperature(, error), units)
    @type teff: 2 or 3 tuple
    @param numax: (numax(, error), units)
    @type numax: 2 or 3 tuple
    @return: log g (and error) in CGS units
    @rtype: 1- or 2-tuple
    """
    numax_sol = convert(constants.numax_sol[-1],'mHz',*constants.numax_sol[:-1],unpack=False)
    #-- take care of temperature
    if len(teff)==3:
        teff = (unumpy.uarray([teff[0],teff[1]]),teff[2])
    teff = convert(teff[-1],'5777K',*teff[:-1],unpack=False)
    #-- take care of numax
    if len(numax)==3:
        numax = (unumpy.uarray([numax[0],numax[1]]),numax[2])
    numax = convert(numax[-1],'mHz',*numax[:-1],unpack=False)
    #-- calculate surface gravity in logarithmic CGS units
    GG = convert(constants.GG_units,'Rsol3 Msol-1 s-2',constants.GG)
    surf_grav = GG*sqrt(teff)*numax / numax_sol
    logg = convert('Rsol s-2',unit,surf_grav)
    return logg     

def derive_mass(surface_gravity,radius,unit='kg'):
    """
    Convert surface gravity and radius to stellar mass.
    """
    #-- take care of logg
    if len(surface_gravity)==3:
        surface_gravity = (unumpy.uarray([surface_gravity[0],surface_gravity[1]]),surface_gravity[2])
    grav = convert(surface_gravity[-1],'m/s2',*surface_gravity[:-1],unpack=False)
    #-- take care of radius
    if len(radius)==3:
        radius = (unumpy.uarray([radius[0],radius[1]]),radius[2])
    R = convert(radius[-1],'m',*radius[:-1],unpack=False)
    #-- calculate mass in SI
    M = grav*R**2/constants.GG
    return convert('kg',unit,M)
    
def derive_zg(mass, logg, unit='cm s-1', **kwargs):
    """
    Convert mass and stellar surface gravity to gravitational redshift. 
    
    Units given to mass and logg must be understandable by C{convert}.
    
    Gravitational redshift is returned in CGS units unless otherwhise stated in unit.
    
    >>> print derive_zg((0.47, 'Msol'), (5.98, 'cm s-2'), unit='km s-1')
    2.57444756874
    
    @param mass: (mass(, error), units)
    @type mass: 2 or 3 tuple
    @param logg: (logg(, error), units)
    @type logg: 2 or 3 tuple
    @param unit: unit of zg
    @type unit: str
    @return: log g (and error)
    @rtype: 1- or 2-tuple
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'g',*mass[:-1],unpack=False)
    #-- take care of logg
    if len(logg)==3:
        logg = (unumpy.uarray([logg[0],logg[1]]),logg[2])
    g = convert(logg[-1],'cm s-2',*logg[:-1],unpack=False, **kwargs)
    
    zg = 10**g / constants.cc_cgs * np.sqrt(constants.GG_cgs * M / 10**g)
    return convert('cm s-1', unit, zg)

def derive_numax(mass,radius,temperature,unit='mHz'):
    """
    Derive the predicted nu_max according to Kjeldsen and Bedding (1995).
    
    Example: compute the predicted numax for the Sun in mHz
    >>> print derive_numax((1.,'Msol'),(1.,'Rsol'),(5777.,'K'))
    3.05
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'Msol',*mass[:-1],unpack=False)
    #-- take care of radius
    if len(radius)==3:
        radius = (unumpy.uarray([radius[0],radius[1]]),radius[2])
    R = convert(radius[-1],'Rsol',*radius[:-1],unpack=False)
    #-- take care of effective temperature
    if len(temperature)==3:
        temperature = (unumpy.uarray([temperature[0],temperature[1]]),temperature[2])
    teff = convert(temperature[-1],'5777K',*temperature[:-1],unpack=False)
    #-- predict nu_max
    nu_max = M/R**2/sqrt(teff)*3.05
    return convert('mHz',unit,nu_max)

def derive_nmax(mass,radius,temperature):
    """
    Derive the predicted n_max according to Kjeldsen and Bedding (1995).
    
    Example: compute the predicted numax for the Sun in mHz
    >>> print derive_nmax((1.,'Msol'),(1.,'Rsol'),(5777.,'K'))
    21.0
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'Msol',*mass[:-1])
    #-- take care of radius
    if len(radius)==3:
        radius = (unumpy.uarray([radius[0],radius[1]]),radius[2])
    R = convert(radius[-1],'Rsol',*radius[:-1])
    #-- take care of effective temperature
    if len(temperature)==3:
        temperature = unumpy.uarray([temperature[0],temperature[1]])
    teff = convert(temperature[-1],'5777K',*temperature[:-1])
    #-- predict n_max
    n_max = sqrt(M/teff/R)*22.6 - 1.6
    return n_max
    
def derive_Deltanu0(mass,radius,unit='mHz'):
    """
    Derive the predicted large spacing according to Kjeldsen and Bedding (1995).
    
    Example: compute the predicted large spacing for the Sun in mHz
    >>> print derive_Deltanu0((1.,'Msol'),(1.,'Rsol'),unit='muHz')
    134.9
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'Msol',*mass[:-1])
    #-- take care of radius
    if len(radius)==3:
        radius = (unumpy.uarray([radius[0],radius[1]]),radius[2])
    R = convert(radius[-1],'Rsol',*radius[:-1])
    #-- predict large spacing
    Deltanu0 = sqrt(M)*R**(-1.5)*134.9
    return convert('muHz',unit,Deltanu0)

def derive_ampllum(luminosity,mass,temperature,wavelength,unit='ppm'):
    """
    Derive the luminosity amplitude around nu_max of solar-like oscillations.
    
    See Kjeldsen and Bedding (1995).
    
    >>> print derive_ampllum((1.,'Lsol'),(1,'Msol'),(5777.,'K'),(550,'nm'))
    (4.7, 0.3)
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'Msol',*mass[:-1])
    #-- take care of effective temperature
    if len(temperature)==3:
        temperature = unumpy.uarray([temperature[0],temperature[1]])
    teff = convert(temperature[-1],'5777K',*temperature[:-1])
    #-- take care of luminosity
    if len(luminosity)==3:
        luminosity = unumpy.uarray([luminosity[0],luminosity[1]])
    lumi = convert(luminosity[-1],'Lsol',*luminosity[:-1])
    #-- take care of wavelength
    if len(wavelength)==3:
        wavelength = unumpy.uarray([wavelength[0],wavelength[1]])
    wave = convert(wavelength[-1],'550nm',*wavelength[:-1])
    ampllum = lumi / wave / teff**2 / M * ufloat((4.7,0.3))
    return convert('ppm',unit,ampllum)

def derive_ampllum_from_velo(velo,temperature,wavelength,unit='ppm'):
    """
    Derive the luminosity amplitude around nu_max of solar-like oscillations.
    
    See Kjeldsen and Bedding (1995).
    
    >>> print derive_ampllum_from_velo((1.,'m/s'),(5777.,'K'),(550,'nm'))
    (20.1, 0.05)
    """
    #-- take care of velocity
    if len(velo)==3:
        velo = (unumpy.uarray([velo[0],velo[1]]),velo[2])
    velo = convert(velo[-1],'m/s',*velo[:-1])
    #-- take care of effective temperature
    if len(temperature)==3:
        temperature = unumpy.uarray([temperature[0],temperature[1]])
    teff = convert(temperature[-1],'5777K',*temperature[:-1])
    #-- take care of wavelength
    if len(wavelength)==3:
        wavelength = unumpy.uarray([wavelength[0],wavelength[1]])
    wave = convert(wavelength[-1],'550nm',*wavelength[:-1])
    ampllum = velo / wave / teff**2 * ufloat((20.1,0.05)) #not sure about error
    return convert('ppm',unit,ampllum)

def derive_amplvel(luminosity,mass,unit='cm/s'):
    """
    Derive the luminosity amplitude around nu_max of solar-like oscillations.
    
    See Kjeldsen and Bedding (1995).
    
    >>> print derive_amplvel((1.,'Lsol'),(1,'Msol'),unit='cm/s')
    (23.4, 1.4)
    """
    #-- take care of mass
    if len(mass)==3:
        mass = (unumpy.uarray([mass[0],mass[1]]),mass[2])
    M = convert(mass[-1],'Msol',*mass[:-1])
    #-- take care of luminosity
    if len(luminosity)==3:
        luminosity = unumpy.uarray([luminosity[0],luminosity[1]])
    lumi = convert(luminosity[-1],'Lsol',*luminosity[:-1])
    amplvel = lumi / M * ufloat((23.4,1.4))
    return convert('cm/s',unit,amplvel)

def derive_galactic_uvw(ra, dec, pmra, pmdec, d, vrad, lsr=False, unit='km s-1'):
    """
    Calculate the Galactic space velocity (U,V,W) of a star based on the propper motion,
    location, radial velocity and distance. (U,V,W) are returned in km/s unless stated
    otherwhise in unit.
    
    Follows the general outline of Johnson & Soderblom (1987, AJ, 93,864)
    except that U is positive outward toward the Galactic *anti*center, and 
    the J2000 transformation matrix to Galactic coordinates is taken from  
    the introduction to the Hipparcos catalog.
    
    Uses solar motion from Coskunoglu et al. 2011 MNRAS:
    (U,V,W)_Sun = (-8.5, 13.38, 6.49)
    
    Example for HD 6755:
    
    >>> derive_galactic_uvw((17.42943586, 'deg'), (61.54727506, 'deg'), (628.42, 'mas yr-1'), (76.65, 'mas yr-1'), (139, 'pc'), (-321.4, 'km s-1'), lsr=True)
    (142.66328352779027, -483.55149105148121, 93.216106970932813)
    
    @param ra: Right assension of the star
    @param dec: Declination of the star
    @param pmra: propper motion  in RA
    @param pmdec: propper motion in DEC
    @param d: distance
    @param vrad: radial velocity
    @param lsr: if True, correct for solar motion
    @param unit: units for U,V and W
    
    @return: (U,V,W)
    """
    #-- take care of ra
    if len(ra)==3:
        ra = (unumpy.uarray([ra[0],ra[1]]),ra[2])
    ra = convert(ra[-1],'deg',*ra[:-1])
    #-- take care of dec
    if len(dec)==3:
        dec = (unumpy.uarray([dec[0],dec[1]]),dec[2])
    dec = convert(dec[-1],'deg',*dec[:-1])
    #-- take care of pmra
    if len(pmra)==3:
        pmra = (unumpy.uarray([pmra[0],pmra[1]]),pmra[2])
    pmra = convert(pmra[-1],'mas yr-1',*pmra[:-1])
    #-- take care of pmdec
    if len(pmdec)==3:
        pmdec = (unumpy.uarray([pmdec[0],pmdec[1]]),pmdec[2])
    pmdec = convert(pmdec[-1],'mas yr-1',*pmdec[:-1])
    #-- take care of d
    if len(d)==3:
        d = (unumpy.uarray([d[0],d[1]]),d[2])
    d = convert(d[-1],'pc',*d[:-1])
    #-- take care of pmdec
    if len(vrad)==3:
        vrad = (unumpy.uarray([vrad[0],vrad[1]]),vrad[2])
    vrad = convert(vrad[-1],'km s-1',*vrad[:-1])
    
    plx = 1e3 / d # parallax in mas

    cosd = np.cos(np.radians(dec))
    sind = np.sin(np.radians(dec))
    cosa = np.cos(np.radians(ra))
    sina = np.sin(np.radians(ra))

    k = 4.74047     #Equivalent of 1 A.U/yr in km/s   
    A_G = np.array( [ [ 0.0548755604, +0.4941094279, -0.8676661490],  
                    [ 0.8734370902, -0.4448296300, -0.1980763734], 
                    [ 0.4838350155,  0.7469822445, +0.4559837762] ]).T

    vec1 = vrad
    vec2 = k * pmra / plx
    vec3 = k * pmdec / plx

    u = ( A_G[0,0]*cosa*cosd+A_G[0,1]*sina*cosd+A_G[0,2]*sind)*vec1+ \
        (-A_G[0,0]*sina     +A_G[0,1]*cosa                   )*vec2+ \
        (-A_G[0,0]*cosa*sind-A_G[0,1]*sina*sind+A_G[0,2]*cosd)*vec3
    v = ( A_G[1,0]*cosa*cosd+A_G[1,1]*sina*cosd+A_G[1,2]*sind)*vec1+ \
        (-A_G[1,0]*sina     +A_G[1,1]*cosa                   )*vec2+ \
        (-A_G[1,0]*cosa*sind-A_G[1,1]*sina*sind+A_G[1,2]*cosd)*vec3
    w = ( A_G[2,0]*cosa*cosd+A_G[2,1]*sina*cosd+A_G[2,2]*sind)*vec1+ \
        (-A_G[2,0]*sina     +A_G[2,1]*cosa                   )*vec2+ \
        (-A_G[2,0]*cosa*sind-A_G[2,1]*sina*sind+A_G[2,2]*cosd)*vec3
        
    if lsr:
        #lsr_vel=[-10.0,5.2,7.2] # Dehnen & Binney (1998)
        #lsr_vel=[-11.1, 12.24, 7.25] # Schonrich et al. (2010)
        lsr_vel=[-8.5,13.38,6.49] # Coskunoglu et al. (2011)
        u = u+lsr_vel[0]
        v = v+lsr_vel[1]
        w = w+lsr_vel[2]
        
    return convert('km s-1',unit,u), convert('km s-1',unit,v), convert('km s-1',unit,w)

def convert_Z_FeH(Z=None, FeH=None, Zsun=0.0122):
    """
    Convert between Z and [Fe/H] using the conversion of Bertelli et al. (1994).
    Provide one of the 2 arguments, and the other will be returned.
    
    log10(Z/Zsun) = 0.977 [Fe/H]
    """
    
    if Z != None:
        return np.log10(Z/Zsun) / 0.977
    else:
        return 10**(0.977*FeH + np.log10(Zsun))
        

#}



#{ Nonlinear change-of-base functions

class NonLinearConverter():
    """
    Base class for nonlinear conversions
    
    This class keeps track of prefix-factors and powers.
    
    To have a real nonlinear converter, you need to define the C{__call__}
    attribute.
    """
    def __init__(self,prefix=1.,power=1.):
        self.prefix = prefix
        self.power = power
    def __rmul__(self,other):
        if type(other)==type(5) or type(other)==type(5.):
            return self.__class__(prefix=self.prefix*other)
    def __div__(self,other):
        if type(other)==type(5) or type(other)==type(5.):
            return self.__class__(prefix=self.prefix*other)
    def __pow__(self,other):
        if type(other)==type(5) or type(other)==type(5.):
            return self.__class__(prefix=self.prefix,power=self.power+other)

class Fahrenheit(NonLinearConverter):
    """
    Convert Fahrenheit to Kelvin and back
    """
    def __call__(self,a,inv=False):
        if not inv: return (a*self.prefix+459.67)*5./9.
        else:       return (a*9./5.-459.67)/self.prefix

class Celcius(NonLinearConverter):
    """
    Convert Celcius to Kelvin and back
    """
    def __call__(self,a,inv=False):
        if not inv: return a*self.prefix+273.15
        else:       return (a-273.15)/self.prefix


class AmplMag(NonLinearConverter):
    """
    Convert a Vega magnitude to W/m2/m (Flambda) and back
    """
    def __call__(self,meas,inv=False):
        #-- this part should include something where the zero-flux is retrieved
        if not inv: return 10**(meas*self.prefix/2.5) - 1.
        else:       return (2.5*log10(1.+meas))/self.prefix

class VegaMag(NonLinearConverter):
    """
    Convert a Vega magnitude to W/m2/m (Flambda) and back
    """
    def __call__(self,meas,photband=None,inv=False,**kwargs):
        #-- this part should include something where the zero-flux is retrieved
        zp = filters.get_info()
        match = zp['photband']==photband.upper()
        if sum(match)==0: raise ValueError("No calibrations for %s"%(photband))
        F0 = convert(zp['Flam0_units'][match][0],'W/m3',zp['Flam0'][match][0])
        mag0 = float(zp['vegamag'][match][0])
        if not inv: return 10**(-(meas-mag0)/2.5)*F0
        else:       return -2.5*log10(meas/F0)+mag0

class ABMag(NonLinearConverter):
    """
    Convert an AB magnitude to W/m2/Hz (Fnu) and back
    """
    def __call__(self,meas,photband=None,inv=False,**kwargs):
        zp = filters.get_info()
        F0 = convert('W/m2/Hz',constants._current_convention,3.6307805477010024e-23)
        match = zp['photband']==photband.upper()
        if sum(match)==0: raise ValueError("No calibrations for %s"%(photband))
        mag0 = float(zp['ABmag'][match][0])
        if np.isnan(mag0): mag0 = 0.
        if not inv:
            try:
                return 10**(-(meas-mag0)/2.5)*F0
            except OverflowError:
                return np.nan
        else:       return -2.5*log10(meas/F0)

class STMag(NonLinearConverter):
    """
    Convert an ST magnitude to W/m2/m (Flambda) and back
    
    mag = -2.5*log10(F) - 21.10
    
    F0 = 3.6307805477010028e-09 erg/s/cm2/AA
    """
    def __call__(self,meas,photband=None,inv=False,**kwargs):
        zp = filters.get_info()
        F0 = convert('erg/s/cm2/AA',constants._current_convention,3.6307805477010028e-09)#0.036307805477010027
        match = zp['photband']==photband.upper()
        if sum(match)==0: raise ValueError("No calibrations for %s"%(photband))
        mag0 = float(zp['STmag'][match][0])
        if np.isnan(mag0): mag0 = 0.
        if not inv: return 10**(-(meas-mag0)/-2.5)*F0
        else:       return -2.5*log10(meas/F0)


class Color(NonLinearConverter):
    """
    Convert a color to a flux ratio and back
    
    B-V = -2.5log10(FB) + CB - (-2.5log10(FV) + CV)
    B-V = -2.5log10(FB) + CB + 2.5log10(FV) - CV
    B-V = -2.5log10(FB/FV) + (CB-CV)
    
    and thus
    
    FB/FV = 10 ** [((B-V) - (CB-CV)) / (-2.5)]
    
    where
    
    CB = 2.5log10[FB(m=0)]
    CV = 2.5log10[FV(m=0)]
    
    Stromgren colour indices:
    
    m1 = v - 2b + y
    c1 = u - 2v + b
    Hbeta = HBN - HBW
    """
    def __call__(self,meas,photband=None,inv=False,**kwargs):
        #-- we have two types of colours: the stromgren M1/C1 type, and the
        #   normal Band1 - Band2 type. We need to have conversions back and
        #   forth: this translates into four cases.
        system,band = photband.split('.')
        if '-' in band and not inv:
            band0,band1 = band.split('-')
            f0 = convert('mag','SI',meas,photband='.'.join([system,band0]),unpack=False)
            f1 = convert('mag','SI',0.00,photband='.'.join([system,band1]))
            return f0/f1
        elif '-' in band and inv:
            #-- the units don't really matter, we choose SI'
            #   the flux ratio is converted to color by assuming that the
            #   denominator flux is equal to one.
            band0,band1 = band.split('-')
            m0 = convert('W/m3','mag',meas,photband='.'.join([system,band0]),unpack=False)
            m1 = convert('W/m3','mag',1.00,photband='.'.join([system,band1]))
            return m0-m1
        elif photband=='STROMGREN.C1' and not inv:
            fu = convert('mag','SI',meas,photband='STROMGREN.U',unpack=False)
            fb = convert('mag','SI',0.00,photband='STROMGREN.B')
            fv = convert('mag','SI',0.00,photband='STROMGREN.V')
            return fu*fb/fv**2
        elif photband=='STROMGREN.C1' and inv:
            mu = convert('W/m3','mag',meas,photband='STROMGREN.U',unpack=False)
            mb = convert('W/m3','mag',1.00,photband='STROMGREN.B')
            mv = convert('W/m3','mag',1.00,photband='STROMGREN.V')
            return mu-2*mv+mb
        elif photband=='STROMGREN.M1' and not inv:
            fv = convert('mag','SI',meas,photband='STROMGREN.V',unpack=False)
            fy = convert('mag','SI',0.00,photband='STROMGREN.Y')
            fb = convert('mag','SI',0.00,photband='STROMGREN.B')
            return fv*fy/fb**2
        elif photband=='STROMGREN.M1' and inv:
            mu = convert('W/m3','mag',meas,photband='STROMGREN.V',unpack=False)
            mb = convert('W/m3','mag',1.00,photband='STROMGREN.Y')
            mv = convert('W/m3','mag',1.00,photband='STROMGREN.B')
            return mv-2*mb+my
        else:
            raise ValueError("No color calibrations for %s"%(photband))

class DecibelSPL(NonLinearConverter):
    """
    Convert a Decibel to intensity W/m2 and back.
    
    This is only valid for Decibels as a sound Pressure level
    """
    def __call__(self,meas,inv=False):
        F0 = 1e-12 # W/m2
        if not inv: return 10**(-meas)*F0
        else:       return log10(meas/F0)

class JulianDay(NonLinearConverter):
    """
    Convert a calender date to Julian date and back
    """
    def __call__(self,meas,inv=False,**kwargs):
        if inv:
            #L= meas+68569
            #N= 4*L//146097
            #L= L-(146097*N+3)//4
            #I= 4000*(L+1)//1461001
            #L= L-1461*I//4+31
            #J= 80*L//2447
            #day = L-2447*J//80+0.5
            #L= J//11
            #month = J+2-12*L
            #year = 100*(N-49)+I+L
            
            j = meas + 0.5 + 32044
            g = np.floor(j/146097)
            dg = np.fmod(j,146097)
            c = np.floor((np.floor(dg/36524) + 1)*3/4)
            dc = dg - c * 36524
            b = np.floor(dc/1461)
            db = np.fmod(dc,1461)
            a = np.floor((np.floor(db/365) + 1)*3/4)
            da = db - a * 365
            y = g*400+c*100+b*4+a
            m = np.floor((da*5+308)/153)-2
            d = da - np.floor((m+4)*153/5) + 124
            year = y - 4800 + np.floor((m+2)/12)
            month = np.fmod(m+2,12)+1
            day = d - 1
            return year,month,day
        else:
            year,month,day = meas[:3]
            hour = len(meas)>3 and meas[3] or 0.
            mint = len(meas)>4 and meas[4] or 0.
            secn = len(meas)>5 and meas[5] or 0.    
            a = (14 - month)//12
            y = year + 4800 - a
            m = month + 12*a - 3
            jd = day + ((153*m + 2)//5) + 365*y + y//4 - y//100 + y//400 - 32045
            jd += hour/24.
            jd += mint/24./60.
            jd += secn/24./3600.
            jd -= 0.5
            return jd

class ModJulianDay(NonLinearConverter):
    """
    Convert a Modified Julian Day to Julian Day  and back
    
    The CoRoT conversion has been checked with the CoRoT data archive: it is
    correct at least to the second (the archive tool's precision).
    """
    ZP = {'COROT':2451545.,
            'HIP':2440000.,
            'MJD':2400000.5,
        'PYEPHEM':2415020.0}
    def __call__(self,meas,inv=False,jtype='MJD'):
        if inv:
            return meas-self.ZP[jtype.upper()]
        else:
            return meas+self.ZP[jtype.upper()]

class GalCoords(NonLinearConverter):
    """
    Convert Galactic coords to complex coords and back
    """
    def __call__(self,mycoord,inv=False,epoch='2000'):
        if inv:
            x,y = mycoord.real,mycoord.imag
            equ = ephem.Equatorial(x,y,epoch=epoch)
            gal = ephem.Galactic(equ,epoch=epoch)
            return gal.lon,gal.lat
        else:
            x,y = mycoord
            gal = ephem.Galactic(x,y,epoch=epoch)
            equ = ephem.Equatorial(gal,epoch=epoch)
            return float(equ.ra) + 1j*float(equ.dec)

class EquCoords(NonLinearConverter):
    """
    Convert Equatorial coords to complex coords and back
    """
    def __call__(self,mycoord,inv=False,epoch='2000'):
        if inv:
            x,y = mycoord.real,mycoord.imag
            equ = ephem.Equatorial(x,y,epoch=epoch)
            return equ.ra,equ.dec
        else:
            x,y = mycoord
            equ = ephem.Equatorial(x,y,epoch=epoch)
            return float(equ.ra) + 1j*float(equ.dec)

class EclCoords(NonLinearConverter):
    """
    Convert Ecliptic coords to complex coords and back
    """
    def __call__(self,mycoord,inv=False,epoch='2000'):
        if inv:
            x,y = mycoord.real,mycoord.imag
            equ = ephem.Equatorial(x,y,epoch=epoch)
            ecl = ephem.Ecliptic(equ,epoch=epoch)
            return ecl.long,ecl.lat
        else:
            x,y = mycoord
            ecl = ephem.Ecliptic(x,y,epoch=epoch)
            equ = ephem.Equatorial(ecl,epoch=epoch)
            return float(equ.ra) + 1j*float(equ.dec)

class DegCoords(NonLinearConverter):
    """
    Convert Complex coords to degrees coordinates and back
    """
    def __call__(self,mycoord,inv=False):
        if inv:
            x,y = mycoord.real,mycoord.imag
            return x/np.pi*180,y/np.pi*180
        else:
            x,y = mycoord
            return x/180.*np.pi + 1j*y/180.*np.pi

class RadCoords(NonLinearConverter):
    """
    Convert Complex coords to degrees coordinates and back
    """
    def __call__(self,mycoord,inv=False):
        if inv:
            x,y = mycoord.real,mycoord.imag
            return x,y
        else:
            x,y = mycoord
            return x + 1j*y

#}
#{ Currencies
#@memoized
def set_exchange_rates():
    """
    Download currency exchange rates from the European Central Bank.
    """
    myurl = 'http://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
    url = urllib.URLopener()
    #url = urllib.request.URLopener() # for Python 3
    #logger.info('Downloading current exchanges rates from ecb.europa.eu')
    filen,msg = url.retrieve(myurl)
    ff = open(filen,'r')
    for line in ff.readlines():
        if '<Cube currency=' in line:
            prefix,curr,interfix,rate,postfix = line.split("'")
            _factors[curr] = (1/float(rate),'EUR','currency','<some currency>')
    ff.close()
    #-- now also retrieve the name of the currencies:
    myurl = 'http://www.ecb.europa.eu/stats/exchange/eurofxref/html/index.en.html'
    url = urllib.URLopener()
    #url = urllib.request.URLopener() # for Python 3
    #logger.info('Downloading information on currency names from ecb.europa.eu')
    filen,msg = url.retrieve(myurl)
    ff = open(filen,'r')
    gotcurr = False
    for line in ff.readlines():
        if gotcurr:
            name = line.split('>')[1].split('<')[0]
            if curr in _factors:
                _factors[curr] = (_factors[curr][0],_factors[curr][1],_factors[curr][2],name)
            gotcurr = False
        if '<td headers="aa" id="' in line:
            curr = line.split('>')[1].split('<')[0]
            gotcurr = True
    ff.close()
    
        
    
            
            
#}

#{ Computations with units
class Unit(object):
    """
    Class to calculate with numbers (and uncertainties) containing units.
    
    You can put Units in an array and calculate with them. It is then allowed
    to mix Units with and without uncertainties. It is also allowed to mix
    Units with different units in one array, though some operations (e.g. sum)
    will not be possible.
    
    Initalisation is done via (see L{__init__} for more options):
    
    >>> a = Unit(2.,'m')
    >>> b = Unit(4.,'km')
    >>> c = Unit(3.,'cm2')
    
    And you can calculate via:
    
    >>> print a*b
    8000.0 m2
    >>> print a/c
    6666.66666667 m-1
    >>> print a+b
    4002.0 m
    
    B{Example 1:} You want to calculate the equatorial velocity of the Sun:
    
    >>> distance = Unit(2*np.pi,'Rsol')
    >>> time = Unit(22.,'d')
    >>> print (distance/time)
    2299.03495719 m1 s-1
    
    or directly to km/s:
    
    >>> print (distance/time).convert('km/s')
    2.29903495719 km/s
    
    and with uncertainties:
    
    >>> distance = Unit((2*np.pi,0.1),'Rsol')
    >>> print (distance/time).convert('km/s')
    2.29903495719+/-0.0365902777778 km/s
    
    B{Example 2}: The surface gravity of the Sun:
    
    >>> G = Unit('GG')
    >>> M = Unit('Msol')
    >>> R = Unit('Rsol')
    >>> logg = np.log10((G*M/R**2).convert('cgs'))
    >>> print logg
    4.43830739117 
    
    or 
    
    >>> G = Unit('GG')
    >>> M = Unit(1.,'Msol')
    >>> R = Unit(1.,'Rsol')
    >>> logg = np.log10((G*M/R**2).convert('cgs'))
    >>> print logg
    4.43830739117 
    
    or 
    
    >>> old = set_convention('cgs')
    
    >>> G = Unit('GG')
    >>> M = Unit((1.,0.01),'Msol')
    >>> R = Unit((1.,0.01),'Rsol')
    >>> logg = np.log10(G*M/R**2)
    >>> print logg
    4.43830739117+/-0.00971111983789 
    
    >>> old = set_convention('SI')
    
    B{Example 3}: The speed of light in vacuum:
    
    >>> eps0 = Unit('eps0')
    >>> mu0 = Unit('mu0')
    >>> cc = Unit('cc')
    >>> cc_ = 1./np.sqrt(eps0*mu0)
    >>> print eps0
    8.85418781762e-12 F m-1
    >>> print mu0
    1.2566370614e-06 T m A-1
    >>> print cc
    299792458.0 m s-1
    >>> print cc_
    299792458.004 m1 s-1
    >>> print cc_/cc
    1.00000000001 
    
    """
    def __new__(cls,*args,**kwargs):
        """
        Create a new Unit instance.
        
        Overriding the default __new__ method makes sure that it is
        possible to initialize a Unit with an existing Unit instance. In
        that case, the original instance will simply be returned, and the
        __init__ functions is smart enough that it will not re-initialize
        the class instance.
        """
        if not isinstance(args[0],Unit):
            return super(Unit, cls).__new__(cls)
        else:
            return args[0]
            
    def __init__(self,value,unit=None,**kwargs):
        """
        Different ways to initialize a Unit.
        
        Without uncertainties:
        
        >>> x1 = Unit(5.,'m')
        >>> x4 = Unit((5.,'m'))
        >>> x5 = Unit(5.,'length')
        
        The latter only works with the basic names ('length', 'time', 'temperature',
        etc..., and assures that the value is interpreted correctly in the
        current convention (SI,cgs...)
        
        With uncertainties:
        
        >>> x2 = Unit((5.,1.),'m')
        >>> x3 = Unit((5.,1.,'m'))
        
        Initiating with an existing instance will not do anything (not even
        making a copy! Only a new reference to the original object is made):
        
        >>> x6 = Unit(x3)
        
        This is the output from printing the examples above:
        
        >>> print x1
        5.0 m
        >>> print x2
        5.0+/-1.0 m
        >>> print x3
        5.0+/-1.0 m
        >>> print x4
        5.0 m
        >>> print x5
        5.0 m
        >>> print x6
        5.0+/-1.0 m
        """
        if isinstance(value,Unit):
            return None
        #-- input values
        kwargs.setdefault('unpack',False)
        #-- handle different input types -- tuples etc..
        if isinstance(value,tuple) and isinstance(value[-1],str):
            unit = value[-1]
            value = value[:-1]
            if len(value)==1:
                value = value[0]
        #-- set the input values        
        self.value = value
        self.unit = unit
        self.kwargs = kwargs
        #-- make sure we can calculate with defined constants        
        if isinstance(self.value,str) and hasattr(constants,self.value):
            self.unit = getattr(constants,self.value+'_units')
            self.value = getattr(constants,self.value)
        elif isinstance(self.value,str) or isinstance(self.value,tuple):
            try:
                self.value = ufloat(self.value)
            except:
                self.value = unumpy.uarray(self.value)
        
        #-- values and units to work with
        if self.unit is not None:
            #-- perhaps someone says the unit is of "type" length. If so,
            #   take the current conventions' unit of the type
            if not self.unit in _factors and self.unit in _conventions[constants._current_convention]:
                self.unit = _conventions[constants._current_convention][self.unit]
            #-- OK, maybe people are making it really difficult and give their
            #   unit as 'pressure' or so... then we can still try to do
            #   something
            elif not self.unit in _factors:
                for fac in _factors:
                    if self.unit==_factors[fac][2]:
                        self.unit = _factors[fac][1]
                        break
        if self.unit and len(self.unit) and self.unit[0]=='[':
            self.value = 10**self.value
            self.unit = self.unit[1:-1]
    
    def convert(self,unit,compress=True):
        """
        Convert this Unit to different units.
        
        By default, the converted unit will be compressed (i.e. it will
        probably not consist of only basic units, but be more readable).
        """
        if unit in _conventions:
            unit_ = change_convention(unit,self.unit)
        else:
            unit_ = unit
        new_value = convert(self.unit,unit_,self.value,**self.kwargs)
        new_unit = Unit(new_value,unit_)
        if compress:
            new_unit.compress()
        return new_unit
    
    def compress(self):
        """
        Compress current unit to a more readable version.
        """
        self.unit = compress(self.unit)
        return self
    
    def as_tuple(self):
        return self[0],self[1]
    
    def get_value(self):
        """
        Returns (value,error) in case of uncertainties.
        """
        val = unumpy.nominal_values(self.value)
        err = unumpy.std_devs(self.value)
        if not val.shape: val = float(val)
        if not err.shape: err = float(err)
        return val,err
    
    def __getitem__(self,key):
        """
        Implements indexing, [0] returns value, [1] return unit.
        """
        if key==0:
            return self.value
        elif key==1:
            return self.unit
    
    def __lt__(self,other):
        """
        Compare SI-values of Units with eacht other.
        """
        return self.convert('SI')[0]<other.convert('SI')[0]
    
    def __radd__(self,other):
        return self.__add__(other)
    
    def __add__(self,other):
        """
        Add a Unit to a Unit.
        
        You can add a non-Unit to a Unit only if the Unit has an empty unit string.
        """
        unit1 = breakdown(self.unit)[1]
        if not hasattr(other,'unit'):
            unit2 = ''
        else:
            unit2 = breakdown(other.unit)[1]
        if unit1!=unit2:
            raise ValueError('unequal units %s and %s'%(unit1,unit2))
        elif unit2=='':
            return self.value+other
        else:
            other_value = convert(other.unit,self.unit,other.value,unpack=False)
            return Unit(self.value+other_value,self.unit)
    
    def __sub__(self,other):
        """
        Subtract a Unit from a Unit.
        """
        return self.__add__(-1*other)
    
    def __rsub__(self,other):
        """
        Subtract a Unit from a Unit.
        """
        return (self*-1).__radd__(other)
    
    def __mul__(self,other):
        """
        Multiply a Unit with something.
        
        If `something' is a Unit, simply join the two Unit strings and call
        L{breakdown} to collect.
        """
        if hasattr(other,'unit'):
            unit1 = change_convention(constants._current_convention,self.unit)
            unit2 = change_convention(constants._current_convention,other.unit)
            value1 = convert(self.unit,unit1,self.value,unpack=False)
            value2 = convert(other.unit,unit2,other.value,unpack=False)
            new_unit = ' '.join([unit1,unit2])
            fac,new_unit = breakdown(new_unit)
            outcome = value1*value2
        else:
            outcome = other*self.value
            new_unit = self.unit
        return Unit(outcome,new_unit)
    
    def __rmul__(self,other):
        return self.__mul__(other)
    
    def __div__(self,other):
        """
        Divide two units.
        
        >>> x = Unit(5.,'m15')
        >>> y = Unit(2.5,'m6.5')
        >>> print(x/y)
        2.0 m8.5
        """
        if hasattr(other,'unit'):
            unit1 = change_convention(constants._current_convention,self.unit)
            unit2 = change_convention(constants._current_convention,other.unit)
            value1 = convert(self.unit,unit1,self.value,unpack=False)
            value2 = convert(other.unit,unit2,other.value,unpack=False)
            #-- reverse signs in second units
            uni_b_ = ''
            isalpha = True
            prev_char_min = False
            for i,char in enumerate(unit2):
                if char=='-':
                    prev_char_min = True
                    continue
                if isalpha and not char.isalpha() and not prev_char_min:
                    uni_b_ += '-'
                prev_char_min = False
                uni_b_ += char
                isalpha = char.isalpha()
            new_unit = ' '.join([unit1,uni_b_])
            fac,new_unit = breakdown(new_unit)
            outcome = value1/value2
        else:
            outcome = self.value/other
            new_unit = self.unit
        return Unit(outcome,new_unit)
    
    def __rdiv__(self,other):
        if hasattr(other,'unit'):
            unit1 = change_convention(constants._current_convention,self.unit)
            unit2 = change_convention(constants._current_convention,other.unit)
            value1 = convert(self.unit,unit1,self.value,unpack=False)
            value2 = convert(other.unit,unit2,other.value,unpack=False)
            #-- reverse signs in second units
            uni_b_ = ''
            isalpha = True
            for i,char in enumerate(unit2):
                if char=='-': continue
                if isalpha and not char.isalpha():
                    uni_b_ += '-'
                uni_b_ += char
                isalpha = char.isalpha()
            new_unit = ' '.join([unit1,uni_b_])
            fac,new_unit = breakdown(new_unit)
            outcome = value2/value1
        else:
            unit1 = change_convention(constants._current_convention,self.unit)
            value1 = convert(self.unit,unit1,self.value,unpack=False)
            new_unit = ''
            isalpha = True
            prev_char_min = False
            for i,char in enumerate(unit1):
                if char=='-':
                    prev_char_min = True
                    continue
                if isalpha and not char.isalpha() and not prev_char_min:
                    new_unit += '-'
                prev_char_min = False
                new_unit += char
                isalpha = char.isalpha()
            fac,new_unit = breakdown(new_unit)
            outcome = other/value1
        return Unit(outcome,new_unit)
    
    def __pow__(self,power):
        """
        Raise a unit to a power:
        
        >>> x = Unit(5.,'m')
        >>> print(x**0.5)
        2.2360679775 m0.5
        >>> print(x**-1)
        0.2 m-1
        >>> print(x**3)
        125.0 m3
        
        >>> x = Unit(9.,'m11')
        >>> print(x**0.5)
        3.0 m5.5
        """
        unit1 = change_convention(constants._current_convention,self.unit)
        value1 = convert(self.unit,unit1,self.value,unpack=False)
        mycomps = [components(u) for u in unit1.split()]
        mycomps = [(u[0]**power,u[1],u[2]*power) for u in mycomps]
        factor = np.product([u[0] for u in mycomps])
        new_unit = ' '.join(['%s%g'%(u[1],u[2]) for u in mycomps])
        fac,new_unit = breakdown(new_unit)
        return Unit(value1**power,new_unit)
        #new_unit = ' '.join(power*[self._basic_unit])
        #fac,new_unit = breakdown(new_unit)
        #return Unit(self._SI_value**power,new_unit)
    
    def sin(self): return Unit(sin(self.convert('rad').value),'')
    def cos(self): return Unit(cos(self.convert('rad').value),'')
    def tan(self): return Unit(tan(self.convert('rad').value),'')
    def arcsin(self): return Unit(arcsin(self.value),'rad')
    def arccos(self): return Unit(arccos(self.value),'rad')
    def arctan(self): return Unit(arctan(self.value),'rad')
    def log10(self): return Unit(log10(self.value),'')
    def log(self): return Unit(log(self.value),'')
    def exp(self): return Unit(exp(self.value),'')
    def sqrt(self): return self**0.5
            
    def __str__(self):
        return '{0} {1}'.format(self.value,self.unit)
    
    def __repr__(self):
        return "Unit('{value}','{unit}')".format(value=repr(self.value),unit=self.unit)
        
        

#}


            
_fluxcalib = os.path.join(os.path.abspath(os.path.dirname(__file__)),'fluxcalib.dat')
#-- basic units which the converter should know about
_factors = collections.OrderedDict([
# DISTANCE
           ('m',     (  1e+00,       'm','length','meter')), # meter
           ('AA',     (  1e-10,       'm','length','angstrom')), # Angstrom
           ('AU',    (constants.au,    constants.au_units,'length','astronomical unit')), # astronomical unit
           ('pc',    (constants.pc,    constants.pc_units,'length','parsec')), # parsec
           ('ly',    (constants.ly,    constants.ly_units,'length','light year')), # light year
           ('bs',    ( 5e-9,         'm','length','beard second')),
           ('Rsol',  (constants.Rsol,  constants.Rsol_units,'length','Solar radius')), # Solar radius
           ('Rearth',(constants.Rearth,constants.Rearth_units,'length','Earth radius')), # Earth radius
           ('Rjup',  (constants.Rjup,constants.Rjup_units,'length','Jupiter radius')), # Jupiter radius
           ('ft',    (0.3048,        'm','length','foot (international)')), # foot (international)
           ('USft',  (1200./3937.,   'm','length','foot (US)')), # foot (US)
           ('fathom',(1.828803657607315,'m','length','fathom')),
           ('rd',    (5.0292,        'm','length','rod')),
           ('perch',    (5.0292,        'm','length','pole')),
           ('pole',    (5.0292,        'm','length','perch')),
           ('in',    (0.0254,        'm','length','inch (international)')), # inch (international)
           ('mi',    (1609.344,      'm','length','mile (international)')), # mile (international)
           ('USmi',  (1609.344,      'm','length','mile (US)')), # US mile or survey mile or statute mile
           ('nami',  (1852.,      'm','length','nautical mile')), # nautical mile
           ('a0',    (constants.a0,  constants.a0_units,'length','Bohr radius')), # Bohr radius
           ('ell',   (1.143,         'm','length','ell')), # ell
           ('yd',    (0.9144,        'm','length','yard (international)')), # yard (international)
           ('potrzebie',(0.002263348517438173216473,'m','length','potrzebie')),
           ('smoot', (1.7018,        'm','length','smooth')),
           ('fur',   (201.168,      'm','length','furlong')),
           ('ch',    (20.11684,     'm','length','chain')), # (approximate) chain, based on US survey foot
# VELOCITY
           ('mph',   (0.44704,       'm s-1','velocity','miles per hour')),
           ('knot',  (1852./3600.,   'm s-1','velocity','nautical mile per hour')),
# MASS
           ('g',     (  1e-03,       'kg','mass','gram')), # gram
           ('gr',    (6.479891e-5,   'kg','mass','grain')),
           ('ton',   (1e3,           'kg','mass','metric tonne')), # metric ton(ne)
           ('u',     (1.66053892173e-27,'kg','mass','atomic mass')), # atomic mass unit (NIST 2010)
           ('Msol',  (constants.Msol,   constants.Msol_units,'mass','Solar mass')), # Solar mass
           ('Mearth',(constants.Mearth, constants.Mearth_units,'mass','Earth mass')), # Earth mass
           ('Mjup',  (constants.Mjup,   constants.Mjup_units,'mass','Jupiter mass')), # Jupiter mass
           ('Mlun',  (constants.Mlun,   constants.Mlun_units,'mass','Lunar mass')), # Lunar mass
           ('lb',    (0.45359237,    'kg','mass','pound')), # pound
           ('st',    (6.35029318,    'kg','mass','stone')), # stone
           ('ounce', (0.0283495231,  'kg','mass','ounce')), # ounce
           ('mol',   (1./constants.NA,'mol','mass','molar mass')), # not really a mass...
           ('firkin',(40.8233,       'kg','mass','firkin')),
           ('carat', (0.0002,        'kg','mass','carat')), # carat, metric
# TIME
           ('s',     (  1e+00,       's','time','second')),     # second
           ('min',   (  60.,         's','time','minute')),     # minute
           ('h',     (3600.,         's','time','hour')),     # hour 
           ('d',     (86400.,      's','time','day')),     # day
           ('wk',    (7*24*3600.,    's','time','week')),     # week
           ('mo',    (30*24*3600., 's','time','month')),     # month
           ('sidereal', (1.0027379093,'','time','sidereal day')),     # sidereal
           ('yr',    (31557600.0,'s','time','Julian year')),     # year (1 Julian century = 36525 d, see NIST appendix B)
           ('cr',    (36525*86400,'s','time','Julian century')),    # century
           ('hz',    (2*np.pi,         'rad s-1','frequency','Hertz')),# Hertz (periodic phenomena)
#           ('rhz',   (1e+00,         'rad s-1','angular frequency','RadHertz')),# Rad Hertz (periodic phenomena)
           ('Bq',    (1e+00,         's-1','time','Becquerel')), # Becquerel (stochastic or non-recurrent phenomena)
           ('Ci',    (3.7e10,        's-1','time','Curie')),
           ('R',     (2.58e-4,       'C kg-1','roentgen','Roentgen')),
           ('JD',    (1e+00,         'JD','time','Julian day')), # Julian Day
           ('CD',    (JulianDay,     'JD','time','calender day')), # Calender Day
           ('MJD',   (ModJulianDay,  'JD','time','modified Julian day')), # Modified Julian Day
           ('j',     (1/60.,         's','time','jiffy')),  # jiffy
           ('fortnight',(1209600.,    's','second','fortnight')),
# ANGLES
           ('rad',         (1e+00,               'rad','angle','radian')),  # radian
           ('cy',          (2*np.pi,               'rad','angle','cycle')),   # cycle
           ('deg',         (np.pi/180.,          'rad','angle','degree')),  # degree
           ('am',          (np.pi/180./60.,      'rad','angle','arcminute')),  # arcminute
           ('as',          (np.pi/180./3600.,    'rad','angle','arcsecond')),  # arcsecond
           ('sr',          (1,                   'sr','angle','sterradian')), # sterradian #1/39.4784176045
           ('rpm',         (0.104719755,         'rad s-1','angle','revolutions per minute')),# revolutions per minute
# COORDINATES
           ('complex_coord',(1e+00+0*1j, 'complex_coord','coordinate','<own unit>')), # own unit
           ('equatorial',   (EquCoords,  'complex_coord','coordinate','equatorial')), # Equatorial coordinates
           ('galactic',     (GalCoords,  'complex_coord','coordinate','galactic')), # Galactic coordinates
           ('ecliptic',     (EclCoords,  'complex_coord','coordinate','ecliptic')), # Ecliptic coordinates
           ('deg_coord',    (DegCoords,  'complex_coord','coordinate','degrees')), # Coordinates in degrees
           ('rad_coord',    (RadCoords,  'complex_coord','coordinate','radians')), # Coordinates in radians
# FORCE
           ('N',     (1e+00,         'kg m s-2','force','Newton')), # newton
           ('dyn',   (1e-05,         'kg m s-2','force','dyne')), # dyne
# TEMPERATURE
           ('K',      (1e+00,        'K','temperature','Kelvin')), # Kelvin
           ('Far',    (Fahrenheit,   'K','temperature','Fahrenheit')), # Fahrenheit
           ('Cel',    (Celcius,      'K','temperature','Celcius')), # Celcius
           ('Tsol',   (constants.Tsol,constants.Tsol_units,'temperature','Solar temperature')), # solar temperature
# ENERGY & POWER
           ('J',     (  1e+00,       'kg m2 s-2','energy','Joule')), # Joule
           ('W',     (  1e+00,       'kg m2 s-3','power','Watt')), # Watt
           ('erg',   (  1e-07,       'kg m2 s-2','energy','ergon')), # ergon
           ('foe',   (  1e44,        'kg m2 s-2','energy','(ten to the) fifty one ergs')),
           ('eV',    (1.60217646e-19,'kg m2 s-2','energy','electron volt')), # electron volt
           ('cal',   (4.1868,        'kg m2 s-2','energy','small calorie (international table)')),# calorie (International table)
           ('Cal',   (4186.8,        'kg m2 s-2','energy','large calorie (international table)')),# calorie (International table)
           ('Lsol',  (constants.Lsol, constants.Lsol_units,'energy/power','Solar luminosity')), # solar luminosity
           ('hp',    (745.699872,    'kg m2 s-3','power','Horsepower')), # horsepower
           ('dp',    (250.,          'kg m2 s-3','power','Donkeypower')),
           ('Gy',    (1.,            'm2 s-2','absorbed dose','gray')), # absobred dose, specific energy (imparted) or kerma
           ('Sv',    (1.,            'm2 s-2','dose equivalent','sievert')),
           ('kat',   (1.,            'mol s-1','catalytic activity','katal')),
           ('rem',   (1e-2,          'm2 s-2','dose equivalent','rem')),
           ('dB',    (DecibelSPL,    'kg s-3','sound intensity','Decibel')),  # W/m2
# VELOCITY
           ('cc',  (constants.cc, constants.cc_units,'m/s','Speed of light')),
# ACCELERATION
           ('Gal', (1.,              'm s-2','acceleration','Gal')),
# PRESSURE
           ('Pa',    (  1e+00,       'kg m-1 s-2','pressure','Pascal')), # Pascal
           ('bar',   (  1e+05,       'kg m-1 s-2','pressure','baros')), # baros
           ('ba',    (  0.1,         'kg m-1 s-2','pressure','barye')), # Barye
           ('at',    (  98066.5,     'kg m-1 s-2','pressure','atmosphere (technical)')), # atmosphere (technical)
           ('atm',   ( 101325,       'kg m-1 s-2','pressure','atmosphere (standard)')), # atmosphere (standared)
           ('torr',  (    133.322,   'kg m-1 s-2','pressure','Torricelli')), # Torricelli
           ('psi',   (   6894.,      'kg m-1 s-2','pressure','pound per square inch')), # pound per square inch
           ('mmHg',  (133.322,       'kg m-1 s-2','pressure','millimeter of mercury')),
           ('P',     (     0.1,      'kg m-1 s-1','dynamic viscosity','poise')), # CGS
           ('St',    (  1e-4,        'm2 s-1',    'kynamatic viscosity','stokes')), # CGS
# MAGNETISM & Electricity
           ('A',     (      1.,      'A',   'electric current','Ampere')),
           ('Gi',    (      0.7957747,      'A',   'electric current','Ampere')), # (approximate)
           ('Bi',    (      10.,     'A',   'electric current','biot')),
           ('C',     (      1.,      'A s', 'electric charge','Coulomb')),
           ('T',     (      1.,      'kg s-2 A-1','magnetic field strength','Tesla')),
           ('G',     (      1e-4,    'kg s-2 A-1','magnetic field strength','Gauss')),
           ('Oe',    (1000/(4*np.pi),'A m-1','magnetizing field','Oersted')),
           ('V',     (      1.,      'kg m2 s-3 A-1','electric potential difference','Volt')),
           ('F',     (      1.,      'kg-1 m-2 s4 A2','electric capacitance','Farad')),
           ('O',     (      1.,      'kg m2 s-3 A-2','electric resistance','Ohm')),
           ('S',     (      1.,      'kg-1 m-2 s3 A2','electric conductance','Siemens')),
           ('Wb',    (      1.,      'kg m2 s-2 A-1','magnetic flux','Weber')),
           ('Mx',    (      1e-8,    'kg m2 s-2 A-1','magnetic flux','Maxwell')),
           ('H',     (      1.,      'kg m2 s-2 A-2','inductance','Henry')),
           ('lm',    (      1.,      'cd sr','luminous flux','lumen')),
           ('lx',    (      1.,      'cd sr m-2','illuminance','lux')),
           ('sb',    (      1e4,     'cd m-2','illuminance','stilb')),
           ('ph',    (      1e4,     'cd sr m-2','illuminance','phot')),
# AREA & VOLUME
           ('ac',    (4046.873,  'm2','area','acre (international)')), # acre (international)
           ('a',     (100.,          'm2','area','are')), # are
           ('b',     (1e-28,         'm2','area','barn')), # barn
           ('l',     (  1e-3,        'm3','volume','liter')),
           ('gal',   (4.54609e-3,    'm3','volume','gallon (Canadian and UK imperial)')),
           ('USgal', (3.785412e-3,   'm3','volume','gallon (US)')),
           ('gi',    (1.420643e-4,   'm3','volume','gill (Canadian and UK imperial)')),
           ('USgi',  (1.182941e-4,   'm3','volume','gill (Canadian and UK imperial)')),
           ('bbl',   (0.1589873,     'm3','volume','barrel')), # (approximate) US barrel)
           ('bu',    (0.03523907,    'm3','volume','bushel')), # (approximate) US bushel
           ('ngogn', (1.1594560721765171e-05,'m3','volume','1000 cubic potrzebies')),
# FLUX
# -- absolute magnitudes
           ('Jy',      (1e-26/(2*np.pi),'kg s-2 rad-1','flux density','Jansky')), # W/m2/Hz
           ('fu',      (1e-26/(2*np.pi),'kg s-2 rad-1','flux density','flux unit')), # W/m2/Hz
           ('vegamag', (VegaMag,       'kg m-1 s-3','flux','Vega magnitude')),  # W/m2/m
           ('mag',     (VegaMag,       'kg m-1 s-3','flux','magnitude')),  # W/m2/m
           ('STmag',   (STMag,         'kg m-1 s-3','flux','ST magnitude')),  # W/m2/m
           ('ABmag',   (ABMag,         'kg s-2 cy-1','flux','AB magnitude')), # W/m2/Hz
# -- magnitude differences (colors)
           ('mag_color',(Color,         'flux_ratio','flux','color')),
           ('flux_ratio',(1+00,         'flux_ratio','flux','flux ratio')),
# -- magnitude amplitudes
           ('ampl',    (1e+00,         'ampl','flux','fractional amplitude')),
           ('Amag',    (AmplMag,       'ampl','flux','amplitude in magnitude')),
           ('pph',     (1e-02,         'ampl','flux','amplitude in parts per hundred')), # amplitude
           ('ppt',     (1e-03,         'ampl','flux','amplitude in parts per thousand')), # amplitude
           ('ppm',     (1e-06,         'ampl','flux','amplitude in parts per million')), # amplitude
# -- currency
           ('EUR',     (1e+00,         'EUR','currency','EURO'))
           ])
#-- set of conventions:
_conventions = {'SI': dict(mass='kg',length='m', time='s',temperature='K',
                          electric_current='A',lum_intens='cd',amount='mol',currency='EUR'), # International standard
               'cgs':dict(mass='g', length='cm',time='s',temperature='K',
                          electric_current='A',lum_intens='cd',amount='mol',currency='EUR'), # Centi-gramme-second
               'sol':dict(mass='Msol',length='Rsol',time='s',temperature='Tsol',
                          electric_current='A',lum_intens='cd',amount='mol',currency='EUR'), # solar
               'imperial':dict(mass='lb',length='yd',time='s',temperature='K',
                          electric_current='A',lum_intens='cd',amount='mol',currency='GBP'), # Imperial (UK/US) system
               }           

#-- some names of combinations of units
_names = {'m1':'length',
          's1':'time',
          'kg1':'mass',
          'rad':'angle',
          'deg':'angle',
          'K':'temperature',
          'm1 s-2':'acceleration',
          'kg1 s-2':'surface tension',
          'cy-1 kg1 s-2':'flux density', # W/m2/Hz 
          'kg1 m-1 s-3':'flux density', # W/m3
          'kg1 s-3':'flux', # W/m2
          'cy1 s-1':'frequency',
          'cy-1 kg1 rad-2 s-2':'specific intensity', # W/m2/Hz/sr
          'kg1 m-1 rad-2 s-3':'specific intensity', # W/m3/sr
          'kg1 rad-2 s-3':'total intensity', # W/m2/sr
          'kg1 m2 s-3':'luminosity', # W or power
          'm1 s-1':'velocity',
          'kg1 m-1 s-2':'pressure',
          'm2':'area',
          'm3':'volume',
          'kg1 m2 s-2':'energy',
          'kg1 m1 s-2':'force',
          'A':'electric current',
          'A s':'electric charge',
          'kg s-2 A-1':'magnetic field strength',
          'A m-1':'magnetizing field',
          'kg m2 s-3 A-1':'electric potential difference',
          'kg-1 m-2 s4 A2':'electric capacitance',
          'kg m2 s-3 A-2':'electric resistance',
          'kg-1 m-2 s3 A2':'electric conductance',
          'kg m2 s-2 A-1':'magnetic flux',
          'kg m2 s-2 A-2':'inductance',
          'cd':'luminous flux',
          'm-2 cd':'illuminance',
          }

#-- scaling factors for prefixes            
_scalings ={'y':       1e-24, # yocto
            'z':       1e-21, # zepto
            'a':       1e-18, # atto
            'f':       1e-15, # femto
            'p':       1e-12, # pico
            'n':       1e-09, # nano
            'mu':      1e-06, # micro
            'u':       1e-06, # micro
            'm':       1e-03, # milli
            'c':       1e-02, # centi
            'd':       1e-01, # deci
            'da':      1e+01, # deka
            'h':       1e+02, # hecto
            'k':       1e+03, # kilo
            'M':       1e+06, # mega
            'G':       1e+09, # giga
            'T':       1e+12, # tera
            'P':       1e+15, # peta
            'E':       1e+18, # exa
            'Z':       1e+21, # zetta
            'Y':       1e+24  # yotta
            }
 
#-- some common aliases
_aliases = [('micron','mum'),('au','AU'),('lbs','lb'),
            ('micro','mu'),('milli','m'),('kilo','k'),('mega','M'),('giga','G'),
            ('nano','n'),('dyne','dyn'),
            ('watt','W'),('Watt','W'),
            ('Hz','hz'), ('liter','l'),('litre','l'),
            ('Tesla','T'),('Ampere','A'),('Coulomb','C'),
            ('joule','J'),('Joule','J'),
            ('jansky','Jy'),('Jansky','Jy'),('jy','Jy'),
            ('arcsec','as'),('arcmin','am'),
            ('cycles','cy'),('cycle','cy'),('cyc','cy'),
            ('angstrom','AA'),('Angstrom','AA'),
            ('inch','in'),('stone','st'),
            ('^',''),('**',''),
            ('celcius','C'),('fahrenheit','F'),('hr','h'),
            ('Vegamag','vegamag'),('mile','mi'),
            ('oz','ounce'),('sun','sol'),('_sun','sol'),('_sol','sol'),
            ('solMass','Msol'),('solLum','Lsol'),
            ('pk','hp'),('mph','mi/h'),('f.u.','fu')
            ]
 
#-- Change-of-base function definitions
_switch = {'s1_to_':       distance2velocity, # switch from wavelength to velocity
           's-1_to_':      velocity2distance, # switch from wavelength to velocity
           'm1rad-1_to_':   distance2spatialfreq,  # for interferometry
           'm-1rad1_to_':   spatialfreq2distance, # for interferometry
           'm1rad1s1_to_':     fnu2flambda, # dunno!
           'm-1rad-1s-1_to_': flambda2fnu,# dunno!
           'm1rad-1s1_to_': fnu2flambda, # switch from Fnu to Flam
           'm-1rad1s-1_to_':flambda2fnu, # switch from Flam to Fnu
           'rad-1s1_to_':   fnu2nufnu, # switch from Fnu to nuFnu
           'rad1s-1_to_':   nufnu2fnu, # switch from nuFnu to Fnu
           'm1_to_':       lamflam2flam, # switch from lamFlam to Flam
           'm-1_to_':      flam2lamflam, # switch from Flam to lamFlam
           #'rad2_to_':     per_sr,
           #'rad-2_to_':    times_sr,
           'sr1_to_': per_sr,
           'sr-1_to_': times_sr,
           'rad1_to_':     do_nothing,#per_cy,
           'rad-1_to_':    do_nothing,#times_cy,
           'rad-2sr1_to_': do_nothing,
           'rad2sr-1_to_': do_nothing,
           'rad-1sr1_to_': do_sqrt,
           'rad1sr-1_to_': do_quad,
           'rad-1s2_to_':   period2freq, # same for both, since just inverse
           'rad1s-2_to_':   period2freq,
           #'cy1_to_':      do_nothing,
           #'cy-1_to_':     do_nothing,
           'cy2_to_':      do_nothing,
           'cy-2_to_':     do_nothing,
           }
 
 
if __name__=="__main__":
    
    if not sys.argv[1:]:
        import doctest
        doctest.testmod()
        quit()
    from optparse import OptionParser, Option, OptionGroup
    import datetime
    import copy
    
    #logger = loggers.get_basic_logger()
    
    #-- make sure we can parse strings as Python code
    def check_pythoncode(option, opt, value):
        try:
            return eval(value)
        except ValueError:
            raise OptionValueError(
                "option %s: invalid python code: %r" % (opt, value))

    #-- generate a custom help log
    class MyOption (Option):
        TYPES = Option.TYPES + ("pythoncode",)
        TYPE_CHECKER = copy.copy(Option.TYPE_CHECKER)
        TYPE_CHECKER["pythoncode"] = check_pythoncode   
    usage = "List of available units:\n" + get_help() + "\nUsage: %prog --from=<unit> --to=<unit> [options] value [error]"
    
    #-- define all the input parameters
    parser = OptionParser(option_class=MyOption,usage=usage)
    parser.add_option('--from',dest='_from',type='str',
                        help="units to convert from",default='SI')
    parser.add_option('--to',dest='_to',type='str',
                        help="units to convert to",default='SI')
    
    group = OptionGroup(parser,'Extra quantities when changing base units (e.g. Flambda to Fnu)')
    group.add_option('--wave','-w',dest='wave',type='str',
                        help="wavelength with units (e.g. used to convert Flambda to Fnu)",default=None)
    group.add_option('--freq','-f',dest='freq',type='str',
                        help="frequency (e.g. used to convert Flambda to Fnu)",default=None)
    group.add_option('--photband','-p',dest='photband',type='str',
                        help="photometric passband",default=None)
    parser.add_option_group(group)
    
    #-- prepare inputs for functions
    (options, args) = parser.parse_args()
    options = vars(options)
    _from = options.pop('_from')
    _to = options.pop('_to')
    
    #-- in case of normal floats or floats with errors
    if not any([',' in i for i in args]):
        args = tuple([float(i) for i in args])
    #-- in case of tuples (like coordinates)
    else:
        args = tuple((eval(args[0]),))
    #-- remove None types
    for option in copy.copy(options):
        if options[option] is None:
            options.pop(option)
    #-- set type correctly
    for option in options:
        if isinstance(options[option],str) and ',' in options[option]:
            entry = options[option].split(',')
            options[option] = (float(entry[0]),entry[1])
    
    #-- check if currencies are asked. If so, download the latest exchange rates
    #   we cannot exactly know if something is a currency before we download all
    #   the names, and we don't want to download the names if no currencies are
    #   given. Therefore, we make an educated guess here:
    from_is_probably_currency = (hasattr(_from,'isupper') and _from.isupper() and len(_from)==3)
    to_is_probably_currency = (hasattr(_to,'isupper') and _to.isupper() and len(_to)==3)
    if from_is_probably_currency or to_is_probably_currency:
        set_exchange_rates()
    
    #-- do the conversion
    output = convert(_from,_to,*args,**options)
    
    ##-- and nicely print to the screen
    if _to=='SI':
        fac,_to = breakdown(_from)
    if _from=='SI':
        fac,_from = breakdown(_to)
    if isinstance(output,tuple) and len(output)==2 and len(args)==2:
        print(("%g +/- %g %s    =    %g +/- %g %s"%(args[0],args[1],_from,output[0],output[1],_to)))
    elif isinstance(output,tuple) and len(output)==2:
        print(("%s %s    =    %s,%s %s"%(args[0],_from,output[0],output[1],_to)))
    elif _to.lower()=='cd':
        year,month,day = output
        year,month = int(year),int(month)
        day,fraction = int(day),day-int(day)
        hour = fraction*24
        hour,fraction = int(hour),hour-int(hour)
        minute = fraction*60
        minute,fraction = int(minute),minute-int(minute)
        second = int(fraction*60)
        dt = datetime.datetime(year,month,day,hour,minute,second)
        print(("%.10g %s    =    %s %s (YYYY-MM-DD HH:MM:SS)"%(args[0],_from,dt,_to)))
    else:
        print(("%.10g %s    =    %.10g %s"%(args[0],_from,output,_to)))
