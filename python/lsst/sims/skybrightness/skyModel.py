import numpy as np
import ephem
from lsst.sims.maf.utils.telescopeInfo import TelescopeInfo
from lsst.sims.utils import haversine, _raDecFromAltAz, _altAzPaFromRaDec
import warnings
from lsst.sims.skybrightness.utils import wrapRA,  mjd2djd
from .interpComponents import ScatteredStar,Airglow,LowerAtm,UpperAtm,MergedSpec,TwilightInterp,MoonInterp,ZodiacalInterp
from lsst.sims.photUtils import Sed

def justReturn(input):
    """
    really, just return the input
    """
    return input


class SkyModel(object):

    def __init__(self, observatory='LSST',
                 twilight=True, zodiacal=True,  moon=True,
                 airglow=True, lowerAtm=False, upperAtm=False, scatteredStar=False,
                 mergedSpec=True, mags=False):
        """
        Instatiate the SkyModel. This loads all the required template spectra/magnitudes
        that will be used for interpolation.

        Observatory: object with attributes lat,lon,elev. But default loads LSST.
        twilight: Include twilight component (True)
        zodiacal: Include zodiacal light component (True)
        moon: Include scattered moonlight compoennt (True)
        airglow: Include airglow component (True)
        lowerAtm: Include lower atmosphere component (False). This component is part of mergedSpec.
        upperAtm: Include upper atmosphere component (False). This component is part of mergedSpec.
        scatteredStar: Include scattered starlight component (False). This component is part of mergedSpec.
        mergedSpec: Compute the lowerAtm, upperAtm, and scatteredStar simultaneously since they are all
        functions of only airmass (True).
        mags: (False) By default, the sky model computes a 17,001 element spectrum. If mags is true,
              the model will return the LSST ugrizy magnitudes.
        """

        self.moon=moon
        self.lowerAtm = lowerAtm
        self.twilight = twilight
        self.zodiacal = zodiacal
        self.upperAtm = upperAtm
        self.airglow = airglow
        self.scatteredStar = scatteredStar
        self.mergedSpec = mergedSpec
        self.mags = mags

        if self.mags:
            self.npix = 6
        else:
            self.npix = 17001

        self.components = {'moon':self.moon, 'lowerAtm':self.lowerAtm, 'twilight':self.twilight,
                           'upperAtm':self.upperAtm, 'airglow':self.airglow,'zodiacal':self.zodiacal,
                           'scatteredStar':self.scatteredStar, 'mergedSpec':self.mergedSpec}

        # Check that the merged component isn't being run with other components
        mergedComps = [self.lowerAtm, self.upperAtm, self.scatteredStar]
        for comp in mergedComps:
            if comp & self.mergedSpec:
                warnings.warn("Adding component multiple times to the final output spectra.")

        interpolators = {'scatteredStar':ScatteredStar, 'airglow':Airglow, 'lowerAtm':LowerAtm,
                         'upperAtm':UpperAtm, 'mergedSpec':MergedSpec, 'moon':MoonInterp,
                         'zodiacal':ZodiacalInterp, 'twilight':TwilightInterp}

        # Load up the interpolation objects for each component
        self.interpObjs = {}
        for key in self.components:
            if self.components[key]:
                self.interpObjs[key] = interpolators[key](mags=self.mags)

        # Set up a pyephem observatory object
        if observatory == 'LSST':
            self.telescope = TelescopeInfo(observatory)
            self.Observatory = ephem.Observer()
            self.Observatory.lat = self.telescope.lat
            self.Observatory.lon = self.telescope.lon
            self.Observatory.elevation = self.telescope.elev
        else:
            self.Observatory = observatory

        # Note that observing conditions have not been set
        self.paramsSet = False

    def setComponents(self, twilight=True, zodiacal=True,  moon=True,
                      airglow=True, lowerAtm=False, upperAtm=False, scatteredStar=False,
                      mergedSpec=True):
        """
        Convience function for turning on/off different sky components.
        """
        self.moon=moon
        self.lowerAtm = lowerAtm
        self.twilight = twilight
        self.zodiacal = zodiacal
        self.upperAtm = upperAtm
        self.airglow = airglow
        self.scatteredStar = scatteredStar
        self.mergedSpec = mergedSpec

    def _initPoints(self):
        """
        Set up an array for all the interpolation points
        """

        names = ['airmass', 'nightTimes', 'alt', 'az', 'azRelMoon', 'moonSunSep', 'moonAltitude',
                 'altEclip', 'azEclipRelSun', 'sunAlt', 'azRelSun', 'solarFlux']
        types = [float]*len(names)
        self.points = np.zeros(self.npts, zip(names,types))


    def setRaDecMjd(self,lon,lat,mjd,degrees=False,azAlt=False,solarFlux=130.):
        """
        Set the sky parameters by computing the sky conditions on a given MJD and sky location.

        lon: Longitude-like (RA or Azimuth). Can be single number, list, or numpy array
        lat: Latitude-like (Dec or Altitude)
        mjd: Modified Julian Date for the calculation. Must be single number.
        degrees: (False) Assumes lon and lat are radians unless degrees=True
        azAlt: (False) Assume lon,lat are RA,Dec unless azAlt=True
        solarFlux: solar flux in SFU Between 50 and 310. Default=130. 1 SFU=10^4 Jy.
        """
        # Wrap in array just in case single points were passed
        if not type(lon).__module__ == np.__name__ :
            if np.size(lon) == 1:
                lon = np.array([lon]).ravel()
                lat = np.array([lat]).ravel()
            else:
                lon = np.array(lon)
                lat = np.array(lat)
        if degrees:
            self.ra = np.radians(lon)
            self.dec = np.radians(lat)
        else:
            self.ra = lon
            self.dec = lat
        if np.size(mjd) > 1:
            raise ValueError('mjd must be single value.')
        self.mjd = mjd
        if azAlt:
            self.azs = self.ra.copy()
            self.alts = self.dec.copy()
            self.ra,self.dec = _raDecFromAltAz(self.alts,self.azs, self.Observatory.lon,
                                               self.Observatory.lat, self.mjd)
        else:
            self.alts,self.azs,pa = _altAzPaFromRaDec(self.ra, self.dec, self.Observatory.lon,
                                                      self.Observatory.lat, self.mjd)

        self.npts = self.ra.size
        self._initPoints()

        self.solarFlux = solarFlux
        self.points['solarFlux'] = self.solarFlux

        # Switch to Dublin Julian Date for pyephem
        self.Observatory.date = mjd2djd(self.mjd)

        sun = ephem.Sun()
        sun.compute(self.Observatory)
        self.sunAlt = sun.alt
        self.sunAz = sun.az

        # Compute airmass the same way as ESO model
        self.airmass = 1./np.cos(np.pi/2.-self.alts)

        self.points['airmass'] = self.airmass
        self.points['nightTimes'] = 2
        self.points['alt'] = self.alts
        self.points['az'] = self.azs

        if self.twilight:
            self.points['sunAlt'] = self.sunAlt
            self.points['azRelSun'] = wrapRA(self.azs - self.sunAz)

        if self.moon:
            moon = ephem.Moon()
            moon.compute(self.Observatory)
            self.moonPhase = moon.phase
            self.moonAlt = moon.alt
            self.moonAz = moon.az
            # Calc azimuth relative to moon
            self.azRelMoon = wrapRA(self.azs - self.moonAz)
            over = np.where(self.azRelMoon > np.pi)
            self.azRelMoon[over] = 2.*np.pi - self.azRelMoon[over]
            self.points['moonAltitude'] += np.degrees(self.moonAlt)
            self.points['azRelMoon'] += self.azRelMoon
            self.points['moonSunSep'] += self.moonPhase/100.*180.


        if self.zodiacal:
            self.eclipLon = np.zeros(self.npts)
            self.eclipLat = np.zeros(self.npts)

            for i,temp in enumerate(self.ra):
                eclip = ephem.Ecliptic(ephem.Equatorial(self.ra[i],self.dec[i], epoch='2000'))
                self.eclipLon[i] += eclip.lon
                self.eclipLat[i] += eclip.lat
            # Subtract off the sun ecliptic longitude
            sunEclip = ephem.Ecliptic(sun)
            self.sunEclipLon = sunEclip.lon
            self.points['altEclip'] += self.eclipLat
            self.points['azEclipRelSun'] += wrapRA(self.eclipLon - self.sunEclipLon)

        self.paramsSet = True
        # Interpolate the templates to the set paramters
        self.interpSky()

    def setParams(self, airmass=1.,azs=90., alts=None, moonPhase=31.67, moonAlt=45.,
                  moonAz=0., sunAlt=-12., sunAz=0., sunEclipLon=0.,
                  eclipLon=135., eclipLat=90., degrees=True, solarFlux=130.):
        """
        Set paramters manually. Note, you can put in unphysical combinations of paramters if you want
        to (e.g., put a full moon at zenith at sunset).
        if the alts kwarg is set it will override the airmass kwarg.
        MoonPhase is percent of moon illuminated (0-100)
        """

        # Convert all values to radians for internal use.
        if degrees:
            convertFunc = np.radians
        else:
            convertFunc = justReturn

        self.solarFlux=solarFlux
        self.sunAlt = convertFunc(sunAlt)
        self.moonPhase = moonPhase
        self.moonAlt = convertFunc(moonAlt)
        self.moonAz = convertFunc(moonAz)
        self.eclipLon = convertFunc(eclipLon)
        self.eclipLat = convertFunc(eclipLat)
        self.sunEclipLon = convertFunc(sunEclipLon)
        self.azs = convertFunc(azs)
        if alts is not None:
            self.airmass = 1./np.cos(np.pi/2.-convertFunc(alts))
            self.alts = convertFunc(alts)
        else:
            self.airmass = airmass
            self.alts = np.pi/2.-np.arccos(1./airmass)
        self.moonTargSep = haversine(azs, alts, moonAz, self.moonAlt)
        self.npts = np.size(airmass)
        self._initPoints()

        self.points['airmass'] = self.airmass
        self.points['nightTimes'] = 2
        self.points['alt'] = self.alts
        self.points['az'] = self.azs
        self.azRelMoon = wrapRA(self.azs - self.moonAz)
        over = np.where(self.azRelMoon > np.pi)
        self.azRelMoon[over] = 2.*np.pi - self.azRelMoon[over]
        self.points['moonAltitude'] += np.degrees(self.moonAlt)
        self.points['azRelMoon'] = self.azRelMoon
        self.points['moonSunSep'] += self.moonPhase/100.*180.

        self.eclipLon = convertFunc(eclipLon)
        self.eclipLat = convertFunc(eclipLat)

        self.sunEclipLon = convertFunc(sunEclipLon)
        self.points['altEclip'] += self.eclipLat
        self.points['azEclipRelSun'] += wrapRA(self.eclipLon - self.sunEclipLon)

        self.sunAz = convertFunc(sunAz)
        self.points['sunAlt'] = self.sunAlt
        self.points['azRelSun'] = wrapRA(self.azs - self.sunAz)
        self.points['solarFlux'] = solarFlux

        self.paramsSet = True
        # Interpolate the templates to the set paramters
        self.interpSky()

    def interpSky(self):
        """
        Interpolate the template spectra to the set RA,Dec and MJD.

        the results are stored as attributes of the class:
        .wave = the wavelength in nm
        .spec = array of spectra with units of ergs/s/cm^2/nm
        """

        if not self.paramsSet:
            raise ValueError('No parameters have been set. Must run setRaDecMjd or setParams before running interpSky.')

        # set up array to hold the resulting spectra for each ra,dec point.
        self.spec = np.zeros((self.npts, self.npix), dtype=float)

        # Rebuild the components dict so things can be turned on/off
        self.components = {'moon':self.moon, 'lowerAtm':self.lowerAtm, 'twilight':self.twilight,
                           'upperAtm':self.upperAtm, 'airglow':self.airglow,'zodiacal':self.zodiacal,
                           'scatteredStar':self.scatteredStar, 'mergedSpec':self.mergedSpec}

        # Loop over each component and add it to the result.
        mask = np.ones(self.npts)
        for key in self.components:
            if self.components[key]:
                result = self.interpObjs[key](self.points)
                # Make sure the component has something
                if np.max(result['spec']) > 0:
                    mask[np.where(np.sum(result['spec'], axis=1) == 0)] = 0
                self.spec += result['spec']
                if not hasattr(self,'wave'):
                    self.wave = result['wave']
                else:
                    if not np.allclose(result['wave'], self.wave, rtol= 1e-5,atol=1e-5):
                        warnings.warn('Wavelength arrays of components do not match.')
        self.spec[np.where(mask == 0),:] = 0

    def returnWaveSpec(self):
        """
        Return the wavelength and spectra
        """
        if self.mags:
            raise ValueError('skyModel has been set to interpolate magnitudes. Initialize object with mags=False')
        return self.wave, self.spec

    def returnMags(self, bandpass=None):
        """
        Convert the computed spectra to magnitudes using the supplied bandpasses,
        or, if self.mags=True, just return the mags in the LSST filters
        """
        if self.mags:
            if bandpass:
                warnings.warn('Ignoring set bandpasses and returning LSST ugrizy.')
            mags = -2.5*np.log10(self.spec)+np.log10(3631.)
        else:
            mags = np.zeros(self.npts, dtype=float)-666
            tempSed = Sed()
            isThrough = np.where(bandpass.sb > 0)
            minWave = bandpass.wavelen[isThrough].min()
            maxWave = bandpass.wavelen[isThrough].max()
            inBand = np.where( (self.wave >= minWave) & (self.wave <= maxWave))
            for i, ra in enumerate(self.ra):
                # Check that there is flux in the band, otherwise calcMag fails
                if np.max(self.spec[i,inBand]) > 0:
                    tempSed.setSED(self.wave, flambda=self.spec[i,:])
                    mags[i] = tempSed.calcMag(bandpass)

        return mags
