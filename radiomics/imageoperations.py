from __future__ import print_function

from itertools import chain
import logging

import numpy
import pywt
import SimpleITK as sitk
import six
from six.moves import range

logger = logging.getLogger(__name__)


def getBinEdges(binwidth, parameterValues):
  r"""
  Calculate and return the histogram using parameterValues (1D array of all segmented voxels in the image). Parameter
  ``binWidth`` determines the fixed width of each bin. This ensures comparable voxels after binning, a fixed bin count
  would be dependent on the intensity range in the segmentation.

  Returns the bin edges, a list of the edges of the calculated bins, length is N(bins) + 1. Bins are defined such, that
  the bin edges are equally spaced from zero, and that the leftmost edge :math:`\leq \min(X_{gl})`.

  *Example: for a ROI with values ranging from 54 to 166, and a bin width of 25, the bin edges will be [50, 75, 100,
  125, 150, 175].*

  This value can be directly passed to ``numpy.histogram`` to generate a histogram or ``numpy.digitize`` to discretize
  the ROI gray values. See also :py:func:`binImage()`.

  References

  - Leijenaar RTH, Nalbantov G, Carvalho S, et al. The effect of SUV discretization in quantitative FDG-PET Radiomics:
    the need for standardized methodology in tumor texture analysis. Sci Rep. 2015;5(August):11075.
  """
  global logger

  # Start binning form the first value lesser than or equal to the minimum value and evenly dividable by binwidth
  lowBound = min(parameterValues) - (min(parameterValues) % binwidth)
  # Add + binwidth to ensure the maximum value is included in the range generated by numpu.arange
  highBound = max(parameterValues) + binwidth

  binEdges = numpy.arange(lowBound, highBound, binwidth)

  # if min(parameterValues) % binWidth = 0 and min(parameterValues) = max(parameterValues), binEdges will only contain
  # 1 value. If this is the case (flat region) ensure that numpy.histogram creates 1 bin (requires 2 edges). For
  # numpy.histogram, a binCount (1) would also suffice, however, this is not accepted by numpy.digitize, which also uses
  # binEdges calculated by this function.
  if len(binEdges) == 1:  # Flat region, ensure that there is 1 bin
    binEdges = [binEdges[0] - .5, binEdges[0] + .5]  # Simulates binEdges returned by numpy.histogram if bins = 1

  logger.debug('Calculated %d bins for bin width %g with edges: %s)', len(binEdges) - 1, binwidth, binEdges)

  return binEdges  # numpy.histogram(parameterValues, bins=binedges)


def binImage(binwidth, parameterMatrix, parameterMatrixCoordinates):
  r"""
  Discretizes the parameterMatrix (matrix representation of the gray levels in the ROI) using the binEdges calculated
  using :py:func:`getBinEdges`. Only voxels defined by parameterMatrixCoordinates (defining the segmentation) are used
  for calculation of histogram and subsequently discretized. Voxels outside segmentation are left unchanged.

  :math:`X_{b, i} = \lfloor \frac{X_{gl, i}}{W} \rfloor - \lfloor \frac {\min(X_{gl})}{W} \rfloor + 1`

  Here, :math:`X_{gl, i}` and :math:`X_{b, i}` are gray level intensities before and after discretization, respectively.
  :math:`{W}` is the bin width value (specfied in ``binWidth`` parameter). The first part of the formula ensures that
  the bins are equally spaced from 0, whereas the second part ensures that the minimum gray level intensity inside the
  ROI after binning is always 1.

  If the range of gray level intensities is equally dividable by the binWidth, i.e. :math:`(\max(X_{gl})- \min(X_{gl}))
  \mod W = 0`, the maximum intensity will be encoded as numBins + 1, therefore the maximum number of gray
  level intensities in the ROI after binning is number of bins + 1.

  .. warning::

    This is different from the assignment of voxels to the bins by ``numpy.histogram`` , which has half-open bins, with
    the exception of the rightmost bin, which means this maximum values are assigned to the topmost bin.
    ``numpy.digitize`` uses half-open bins, including the rightmost bin.
  """
  global logger
  logger.debug('Discretizing gray levels inside ROI')

  binEdges = getBinEdges(binwidth, parameterMatrix[parameterMatrixCoordinates])

  parameterMatrix[parameterMatrixCoordinates] = numpy.digitize(parameterMatrix[parameterMatrixCoordinates], binEdges)

  return parameterMatrix, binEdges


def generateAngles(size, **kwargs):
  r"""
  Generate all possible angles for specified distances in ``distances`` in 3D. E.g. for d = 1, 13 angles are generated
  and for d = 2, 49 angles are generated (representing the 26 connected region for distance 1, and the 98 connected
  region for distance 2). Angles are generated with the following steps:

  1. All angles for distance = 1 to the maximum distance specified in ``distances`` are generated.
  2. Only angles are retained, for which the maximum step size in any dimension (i.e. the infinity norm distance from
     the center voxel) is present in ``distances``.
  3. "Impossible" angles (where 'neighbouring' voxels will always be outside delineation) are deleted.
  4. If ``force2Dextraction`` is enabled, all angles defining a step in the ``force2Ddimension`` are removed
     (e.g. if this dimension is 0, all angles that have a non-zero step size at index 0 (z dimension) are removed,
     resulting in angles that only move in the x and/or y dimension).

  :param size: dimensions (z, x, y) of the bounding box of the tumor mask.
  :param kwargs: The following additional parameters can be specified here (default values in brackets):

    - distances [[1]]: List of integers. This specifies the distances between the center voxel and the neighbor, for
      which angles should be generated.
    - force2D [False]: Boolean, set to true to force a by slice texture calculation. Dimension that identifies
      the 'slice' can be defined in ``force2Ddimension``. If input ROI is already a 2D ROI, features are automatically
      extracted in 2D.
    - force2Ddimension [0]: int, range 0-2. Specifies the 'slice' dimension for a by-slice feature extraction. Value 0
      identifies the 'z' dimension (axial plane feature extraction), and features will be extracted from the xy plane.
      Similarly, 1 identifies the y dimension (coronal plane) and 2 the x dimension (saggital plane). if
      ``force2Dextraction`` is set to False, this parameter has no effect.

  :return: numpy array with shape (N, 3), where N is the number of unique angles
  """
  global logger

  logger.debug('Generating angles')

  distances = kwargs.get('distances', [1])
  force2Dextraction = kwargs.get('force2D', False)
  force2Ddimension = kwargs.get('force2Ddimension', 0)

  maxDistance = max(distances)
  angles = []

  # Generate all possible angles for distance = 1 to maxDistance
  for z in range(1, maxDistance + 1):
    angles.append((0, 0, z))
    for y in range(-maxDistance, maxDistance + 1):
      angles.append((0, z, y))
      for x in range(-maxDistance, maxDistance + 1):
        angles.append((z, y, x))

  if maxDistance > 1:  # multiple distances, check if some angles need to be removed
    angles = numpy.array([angle for angle in angles if numpy.max(numpy.abs(angle)) in distances])
  else:  # all generated angles must be retained
    angles = numpy.array(angles)

  # Remove 'impossible' angles: these angles always point to a 'neighbor' outside the ROI and therefore never yield a
  # valid voxel-pair.
  angles = numpy.delete(angles, numpy.where(numpy.min(size - numpy.abs(angles), 1) <= 0), 0)

  if force2Dextraction:
    # Remove all angles that move in the force2Ddimension, retaining all that move only in the force 2D plane
    angles = numpy.delete(angles, numpy.where(angles[:, force2Ddimension] != 0), 0)

  logger.debug('Generated %d angles', len(angles))

  return angles


def checkMask(imageNode, maskNode, **kwargs):
  """
  Checks whether the Region of Interest (ROI) defined in the mask size and dimensions match constraints, specified in
  settings. The following checks are performed.

  1. Check whether the mask corresponds to the image (i.e. has a similar size, spacing, direction and origin). **N.B.
     This check is performed by SimpleITK, if it fails, an error is logged, with additional error information from
     SimpleITK logged with level DEBUG (i.e. logging-level has to be set to debug to store this information in the log
     file).** The tolerance can be increased using the ``geometryTolerance`` parameter. Alternatively, if the
     ``correctMask`` parameter is ``True``, PyRadiomics will check if the mask contains a valid ROI (inside image
     physical area) and if so, resample the mask to image geometry. See :ref:`radiomics-settings-label` for more info.

  2. Check if the label is present in the mask
  3. Count the number of dimensions in which the size of the ROI > 1 (i.e. does the ROI represent a single voxel (0), a
     line (1), a surface (2) or a volume (3)) and compare this to the minimum number of dimension required (specified in
     ``minimumROIDimensions``).
  4. Optional. Check if there are at least N voxels in the ROI. N is defined in ``minimumROISize``, this test is skipped
     if ``minimumROISize = None``.

  This function returns a tuple of two items. The first item (if not None) is the bounding box of the mask. The second
  item is the mask that has been corrected by resampling to the input image geometry (if that resampling was successful).

  If a check fails, an error is logged and a (None,None) tuple is returned. No features will be extracted for this mask.
  If the mask passes all tests, this function returns the bounding box, which is used in the :py:func:`cropToTumorMask`
  function. 
  
  The bounding box is calculated during (1.) and used for the subsequent checks. The bounding box is
  calculated by SimpleITK.LabelStatisticsImageFilter() and returned as a tuple of indices: (L_x, U_x, L_y, U_y, L_z,
  U_z), where 'L' and 'U' are lower and upper bound, respectively, and 'x', 'y' and 'z' the three image dimensions.

  By reusing the bounding box calculated here, calls to SimpleITK.LabelStatisticsImageFilter() are reduced, improving
  performance.

  Uses the following settings:

  - minimumROIDimensions [1]: Integer, range 1-3, specifies the minimum dimensions (1D, 2D or 3D, respectively).
    Single-voxel segmentations are always excluded.
  - minimumROISize [None]: Integer, > 0,  specifies the minimum number of voxels required. Test is skipped if
    this parameter is set to None.

  .. note::

    If the first check fails there are generally 2 possible causes:

     1. The image and mask are matched, but there is a slight difference in origin, direction or spacing. The exact
        cause, difference and used tolerance are stored with level DEBUG in a log (if enabled). For more information on
        setting up logging, see ":ref:`setting up logging <radiomics-logging-label>`" and the helloRadiomics examples
        (located in the ``pyradiomics/examples`` folder). This problem can be fixed by changing the global tolerance
        (``geometryTolerance`` parameter) or enabling mask correction (``correctMask`` parameter).
     2. The image and mask do not match, but the ROI contained within the mask does represent a physical volume
        contained within the image. If this is the case, resampling is needed to ensure matching geometry between image
        and mask before features can be extracted. This can be achieved by enabling mask correction using the
        ``correctMask`` parameter.
  """
  global logger

  boundingBox = None
  correctedMask = None

  label = kwargs.get('label', 1)
  minDims = kwargs.get('minimumROIDimensions', 1)
  minSize = kwargs.get('minimumROISize', None)

  logger.debug('Checking mask with label %d', label)
  logger.debug('Calculating bounding box')
  # Determine bounds
  lsif = sitk.LabelStatisticsImageFilter()
  try:
    lsif.Execute(imageNode, maskNode)

    # If lsif fails, and mask is corrected, it includes a check whether the label is present. Therefore, perform
    # this test here only if lsif does not fail on the first attempt.
    if label not in lsif.GetLabels():
      logger.error('Label (%g) not present in mask', label)
      return (boundingBox, correctedMask)
  except RuntimeError as e:
    # If correctMask = True, try to resample the mask to the image geometry, otherwise return None ("fail")
    if not kwargs.get('correctMask', False):
      if "Both images for LabelStatisticsImageFilter don't match type or dimension!" in e.message:
        logger.error('Image/Mask datatype or size mismatch. Potential solution: enable correctMask, see '
                     'Documentation:Usage:Customizing the Extraction:Settings:correctMask for more information')
        logger.debug('Additional information on error.', exc_info=True)
      elif "Inputs do not occupy the same physical space!" in e.message:
        logger.error('Image/Mask geometry mismatch. Potential solution: increase tolerance using geometryTolerance, '
                     'see Documentation:Usage:Customizing the Extraction:Settings:geometryTolerance for more '
                     'information')
        logger.debug('Additional information on error.', exc_info=True)
      return (boundingBox, correctedMask)

    logger.warning('Image/Mask geometry mismatch, attempting to correct Mask')

    correctedMask = _correctMask(imageNode, maskNode, label)
    if correctedMask is None:  # Resampling failed (ROI outside image physical space
      logger.error('Image/Mask correction failed, ROI invalid (not found or outside of physical image bounds)')
      return (boundingBox, correctedMask)

    # Resampling succesful, try to calculate boundingbox
    try:
      lsif.Execute(imageNode, correctedMask)
    except RuntimeError:
      logger.error('Calculation of bounding box failed, for more information run with DEBUG logging and check log')
      logger.debug('Bounding box calculation with resampled mask failed', exc_info=True)
      return (boundingBox, correctedMask)

  # LBound and UBound of the bounding box, as (L_X, U_X, L_Y, U_Y, L_Z, U_Z)
  boundingBox = numpy.array(lsif.GetBoundingBox(label))

  logger.debug('Checking minimum number of dimensions requirements (%d)', minDims)
  ndims = numpy.sum((boundingBox[1::2] - boundingBox[0::2] + 1) > 1)  # UBound - LBound + 1 = Size
  if ndims <= minDims:
    logger.error('mask has too few dimensions (number of dimensions %d, minimum required %d)', ndims, minDims)
    return (boundingBox, correctedMask)

  if minSize is not None:
    logger.debug('Checking minimum size requirements (minimum size: %d)', minSize)
    roiSize = lsif.GetCount(label)
    if roiSize <= minSize:
      logger.error('Size of the ROI is too small (minimum size: %g, ROI size: %g', minSize, roiSize)
      return (boundingBox, correctedMask)

  return (boundingBox, correctedMask)


def _correctMask(imageNode, maskNode, label):
  """
  If the mask geometry does not match the image geometry, this function can be used to resample the mask to the image
  physical space.

  First, the mask is checked for a valid ROI (i.e. maskNode contains an ROI with the given label value, which does not
  include areas outside of the physical image bounds).

  If the ROI is valid, the maskNode is resampled using the imageNode as a reference image and a nearest neighbor
  interpolation.

  If the ROI is valid, the resampled mask is returned, otherwise ``None`` is returned.
  """
  global logger
  logger.debug('Resampling mask to image geometry')

  if _checkROI(imageNode, maskNode, label) is None:  # ROI invalid
    return None

  rif = sitk.ResampleImageFilter()
  rif.SetReferenceImage(imageNode)
  rif.SetInterpolator(sitk.sitkNearestNeighbor)

  logger.debug('Resampling...')

  return rif.Execute(maskNode)


def _checkROI(imageNode, maskNode, label):
  """
  Check whether maskNode contains a valid ROI defined by label:

  1. Check whether the label value is present in the maskNode.
  2. Check whether the ROI defined by the label does not include an area outside the physical area of the image.

  For the second check, a tolerance of 1e-3 is allowed.

  If the ROI is valid, the bounding box (lower bounds and size in 3 directions: L_X, L_Y, L_Z, S_X, S_Y, S_Z) is
  returned. Otherwise, ``None`` is returned.
  """
  global logger
  logger.debug('Checking ROI validity')

  # Determine bounds of cropped volume in terms of original Index coordinate space
  lssif = sitk.LabelShapeStatisticsImageFilter()
  lssif.Execute(maskNode)

  logger.debug('Checking if label %d is persent in the mask', label)
  if label not in lssif.GetLabels():
    logger.error('Label (%d) not present in mask', label)
    return None

  # LBound and size of the bounding box, as (L_X, L_Y, L_Z, S_X, S_Y, S_Z)
  bb = numpy.array(lssif.GetBoundingBox(label))

  # Determine if the ROI is within the physical space of the image

  logger.debug('Comparing physical space of bounding box to physical space of image')
  # Step 1: Get the origin and UBound corners of the bounding box in physical space
  # The additional 0.5 represents the difference between the voxel center and the voxel corner
  # Upper bound index of ROI = bb[:3] + bb[3:] - 1 (LBound + Size - 1), .5 is added to get corner
  ROIBounds = (maskNode.TransformContinuousIndexToPhysicalPoint(bb[:3] - .5),  # Origin
               maskNode.TransformContinuousIndexToPhysicalPoint(bb[:3] + bb[3:] - 0.5))  # UBound
  # Step 2: Translate the ROI physical bounds to the image coordinate space
  ROIBounds = (imageNode.TransformPhysicalPointToContinuousIndex(ROIBounds[0]),  # Origin
               imageNode.TransformPhysicalPointToContinuousIndex(ROIBounds[1]))

  logger.debug('ROI bounds (image coordinate space): %s', ROIBounds)

  # Check if any of the ROI bounds are outside the image indices (i.e. -0.5 < ROI < Im.Size -0.5)
  # The additional 0.5 is to allow for different spacings (defines the edges, not the centers of the edge-voxels
  tolerance = 1e-3  # Define a tolerance to correct for machine precision errors
  if numpy.any(numpy.min(ROIBounds, axis=0) < (- .5 - tolerance)) or \
     numpy.any(numpy.max(ROIBounds, axis=0) > (numpy.array(imageNode.GetSize()) - .5 + tolerance)):
    logger.error('Bounding box of ROI is larger than image space:\n\t'
                 'ROI bounds (image coordinate space) %s\n\tImage Size %s', ROIBounds, imageNode.GetSize())
    return None

  logger.debug('ROI valid, calculating resampling grid')

  return bb


def cropToTumorMask(imageNode, maskNode, boundingBox):
  """
  Create a sitkImage of the segmented region of the image based on the input label.

  Create a sitkImage of the labelled region of the image, cropped to have a
  cuboid shape equal to the ijk boundaries of the label.

  :param boundingBox: The bounding box used to crop the image. This is the bounding box as returned by
    :py:func:`checkMask`.
  :param label: [1], value of the label, onto which the image and mask must be cropped.
  :return: Cropped image and mask (SimpleITK image instances).

  """
  global logger

  oldMaskID = maskNode.GetPixelID()
  maskNode = sitk.Cast(maskNode, sitk.sitkInt32)
  size = numpy.array(maskNode.GetSize())

  ijkMinBounds = boundingBox[0::2]
  ijkMaxBounds = size - boundingBox[1::2] - 1

  # Crop Image
  logger.debug('Cropping to size %s', (boundingBox[1::2] - boundingBox[0::2]) + 1)
  cif = sitk.CropImageFilter()
  try:
    cif.SetLowerBoundaryCropSize(ijkMinBounds)
    cif.SetUpperBoundaryCropSize(ijkMaxBounds)
  except TypeError:
    # newer versions of SITK/python want a tuple or list
    cif.SetLowerBoundaryCropSize(ijkMinBounds.tolist())
    cif.SetUpperBoundaryCropSize(ijkMaxBounds.tolist())
  croppedImageNode = cif.Execute(imageNode)
  croppedMaskNode = cif.Execute(maskNode)

  croppedMaskNode = sitk.Cast(croppedMaskNode, oldMaskID)

  return croppedImageNode, croppedMaskNode


def resampleImage(imageNode, maskNode, resampledPixelSpacing, interpolator=sitk.sitkBSpline, label=1, padDistance=5):
  """
  Resamples image and mask to the specified pixel spacing (The default interpolator is Bspline).

  Resampling can be enabled using the settings 'interpolator' and 'resampledPixelSpacing' in the parameter file or as
  part of the settings passed to the feature extractor. See also
  :ref:`feature extractor <radiomics-featureextractor-label>`.

  'imageNode' and 'maskNode' are SimpleITK Objects, and 'resampledPixelSpacing' is the output pixel spacing (sequence of
  3 elements).

  Only part of the image and labelmap are resampled. The resampling grid is aligned to the input origin, but only voxels
  covering the area of the image ROI (defined by the bounding box) and the padDistance are resampled. This results in a
  resampled and partially cropped image and mask. Additional padding is required as some filters also sample voxels
  outside of segmentation boundaries. For feature calculation, image and mask are cropped to the bounding box without
  any additional padding, as the feature classes do not need the gray level values outside the segmentation.

  The resampling grid is calculated using only the input mask. Even when image and mask have different directions, both
  the cropped image and mask will have the same direction (equal to direction of the mask). Spacing and size are
  determined by settings and bounding box of the ROI.

  .. note::
    Before resampling the bounds of the non-padded ROI are compared to the bounds. If the ROI bounding box includes
    areas outside of the physical space of the image, an error is logged and (None, None) is returned. No features will
    be extracted. This enables the input image and mask to have different geometry, so long as the ROI defines an area
    within the image.

  .. note::
    The additional padding is adjusted, so that only the physical space within the mask is resampled. This is done to
    prevent resampling outside of the image. Please note that this assumes the image and mask to image the same physical
    space. If this is not the case, it is possible that voxels outside the image are included in the resampling grid,
    these will be assigned a value of 0. It is therefore recommended, but not enforced, to use an input mask which has
    the same or a smaller physical space than the image.
  """
  global logger
  logger.debug('Resampling image and mask')

  if imageNode is None or maskNode is None:
    return None, None  # this function is expected to always return a tuple of 2 elements

  logger.debug('Comparing resampled spacing to original spacing (image and mask')
  maskSpacing = numpy.array(maskNode.GetSpacing())
  imageSpacing = numpy.array(imageNode.GetSpacing())

  # If current spacing is equal to resampledPixelSpacing, no interpolation is needed
  if numpy.array_equal(maskSpacing, resampledPixelSpacing) and numpy.array_equal(imageSpacing, resampledPixelSpacing):
    logger.info('New spacing equal to old, no resampling required')
    return imageNode, maskNode

  # Check if the maskNode contains a valid ROI. If ROI is valid, the bounding box needed to calculate the resampling
  # grid is returned.
  bb = _checkROI(imageNode, maskNode, label)

  if bb is None:  # ROI invalid
    return None, None

  # Do not resample in those directions where labelmap spans only one slice.
  maskSize = numpy.array(maskNode.GetSize())
  resampledPixelSpacing = numpy.where(bb[3:] != 1, resampledPixelSpacing, maskSpacing)

  spacingRatio = maskSpacing / resampledPixelSpacing

  # Determine bounds of cropped volume in terms of new Index coordinate space,
  # round down for lowerbound and up for upperbound to ensure entire segmentation is captured (prevent data loss)
  # Pad with an extra .5 to prevent data loss in case of upsampling. For Ubound this is (-1 + 0.5 = -0.5)
  bbNewLBound = numpy.floor((bb[:3] - 0.5) * spacingRatio - padDistance)
  bbNewUBound = numpy.ceil((bb[:3] + bb[3:] - 0.5) * spacingRatio + padDistance)

  # Ensure resampling is not performed outside bounds of original image
  maxUbound = numpy.ceil(maskSize * spacingRatio) - 1
  bbNewLBound = numpy.where(bbNewLBound < 0, 0, bbNewLBound)
  bbNewUBound = numpy.where(bbNewUBound > maxUbound, maxUbound, bbNewUBound)

  # Calculate the new size. Cast to int to prevent error in sitk.
  newSize = numpy.array(bbNewUBound - bbNewLBound + 1, dtype='int').tolist()

  # Determine continuous index of bbNewLBound in terms of the original Index coordinate space
  bbOriginalLBound = bbNewLBound / spacingRatio

  # Origin is located in center of first voxel, e.g. 1/2 of the spacing
  # from Corner, which corresponds to 0 in the original Index coordinate space.
  # The new spacing will be in 0 the new Index coordinate space. Here we use continuous
  # index to calculate where the new 0 of the new Index coordinate space (of the original volume
  # in terms of the original spacing, and add the minimum bounds of the cropped area to
  # get the new Index coordinate space of the cropped volume in terms of the original Index coordinate space.
  # Then use the ITK functionality to bring the continuous index into the physical space (mm)
  newOriginIndex = numpy.array(.5 * (resampledPixelSpacing - maskSpacing) / maskSpacing)
  newCroppedOriginIndex = newOriginIndex + bbOriginalLBound
  newOrigin = maskNode.TransformContinuousIndexToPhysicalPoint(newCroppedOriginIndex)

  imagePixelType = imageNode.GetPixelID()
  maskPixelType = maskNode.GetPixelID()

  direction = numpy.array(maskNode.GetDirection())

  logger.info('Applying resampling from spacing %s and size %s to spacing %s and size %s',
              maskSpacing, maskSize, resampledPixelSpacing, newSize)

  try:
    if isinstance(interpolator, six.string_types):
      interpolator = getattr(sitk, interpolator)
  except:
    logger.warning('interpolator "%s" not recognized, using sitkBSpline', interpolator)
    interpolator = sitk.sitkBSpline

  rif = sitk.ResampleImageFilter()

  rif.SetOutputSpacing(resampledPixelSpacing)
  rif.SetOutputDirection(direction)
  rif.SetSize(newSize)
  rif.SetOutputOrigin(newOrigin)

  logger.debug('Resampling image')
  rif.SetOutputPixelType(imagePixelType)
  rif.SetInterpolator(interpolator)
  resampledImageNode = rif.Execute(imageNode)

  logger.debug('Resampling mask')
  rif.SetOutputPixelType(maskPixelType)
  rif.SetInterpolator(sitk.sitkNearestNeighbor)
  resampledMaskNode = rif.Execute(maskNode)

  return resampledImageNode, resampledMaskNode


def normalizeImage(image, scale=1, outliers=None):
  r"""
  Normalizes the image by centering it at the mean with standard deviation. Normalization is based on all gray values in
  the image, not just those inside the segementation.

  :math:`f(x) = \frac{s(x - \mu_x)}{\sigma_x}`

  Where:

  - :math:`x` and :math:`f(x)` are the original and normalized intensity, respectively.
  - :math:`\mu_x` and :math:`\sigma_x` are the mean and standard deviation of the image instensity values.
  - :math:`s` is an optional scaling defined by ``scale``. By default, it is set to 1.

  Optionally, outliers can be removed, in which case values for which :math:`x > \mu_x + n\sigma_x` or
  :math:`x < \mu_x - n\sigma_x` are set to :math:`\mu_x + n\sigma_x` and :math:`\mu_x - n\sigma_x`, respectively.
  Here, :math:`n>0` and defined by ``outliers``. This, in turn, is controlled by the ``removeOutliers`` parameter.
  Removal of outliers is done after the values of the image are normalized, but before ``scale`` is applied.
  """
  global logger
  logger.debug('Normalizing image with scale %d', scale)
  image = sitk.Normalize(image)

  if outliers is not None:
    logger.debug('Removing outliers > %g standard deviations', outliers)
    imageArr = sitk.GetArrayFromImage(image)

    imageArr[imageArr > outliers] = outliers
    imageArr[imageArr < -outliers] = -outliers

    newImage = sitk.GetImageFromArray(imageArr)
    newImage.CopyInformation(image)

  image *= scale

  return image


def applyThreshold(inputImage, lowerThreshold, upperThreshold, insideValue=None, outsideValue=0):
  # this mode is useful to generate the mask of thresholded voxels
  if insideValue:
    tif = sitk.BinaryThresholdImageFilter()
    tif.SetInsideValue(insideValue)
    tif.SetLowerThreshold(lowerThreshold)
    tif.SetUpperThreshold(upperThreshold)
  else:
    tif = sitk.ThresholdImageFilter()
    tif.SetLower(lowerThreshold)
    tif.SetUpper(upperThreshold)
  tif.SetOutsideValue(outsideValue)
  return tif.Execute(inputImage)


def getOriginalImage(inputImage, **kwargs):
  """
  This function does not apply any filter, but returns the original image. This function is needed to
  dyanmically expose the original image as a valid input image.

  :return: Yields original image, 'original' and ``kwargs``
  """
  global logger
  logger.debug('Yielding original image')
  yield inputImage, 'original', kwargs


def getLoGImage(inputImage, **kwargs):
  """
  Apply Laplacian of Gaussian filter to input image and compute signature for each filtered image.

  Following settings are possible:

  - sigma: List of floats or integers, must be greater than 0. Sigma values to
    use for the filter (determines coarseness).

  N.B. Setting for sigma must be provided. If omitted, no LoG image features are calculated and the function
  will return an empty dictionary.

  Returned filter name reflects LoG settings:
  log-sigma-<sigmaValue>-3D.

  :return: Yields log filtered image for each specified sigma, corresponding filter name and ``kwargs``
  """
  global logger

  logger.debug('Generating LoG images')

  # Check if size of image is > 4 in all 3D directions (otherwise, LoG filter will fail)
  size = numpy.array(inputImage.GetSize())
  spacing = numpy.array(inputImage.GetSpacing())

  if numpy.min(size) < 4:
    logger.warning('Image too small to apply LoG filter, size: %s', size)
    return

  sigmaValues = kwargs.get('sigma', [])

  for sigma in sigmaValues:
    logger.info('Computing LoG with sigma %g', sigma)

    if sigma > 0.0:
      if numpy.all(size >= numpy.ceil(sigma / spacing) + 1):
        lrgif = sitk.LaplacianRecursiveGaussianImageFilter()
        lrgif.SetNormalizeAcrossScale(True)
        lrgif.SetSigma(sigma)
        inputImageName = 'log-sigma-%s-mm-3D' % (str(sigma).replace('.', '-'))
        logger.debug('Yielding %s image', inputImageName)
        yield lrgif.Execute(inputImage), inputImageName, kwargs
      else:
        logger.warning('applyLoG: sigma(%g)/spacing(%s) + 1 must be greater than the size(%s) of the inputImage',
                       sigma,
                       spacing,
                       size)
    else:
      logger.warning('applyLoG: sigma must be greater than 0.0: %g', sigma)


def getWaveletImage(inputImage, **kwargs):
  """
  Apply wavelet filter to image and compute signature for each filtered image.

  Following settings are possible:

  - start_level [0]: integer, 0 based level of wavelet which should be used as first set of decompositions
    from which a signature is calculated
  - level [1]: integer, number of levels of wavelet decompositions from which a signature is calculated.
  - wavelet ["coif1"]: string, type of wavelet decomposition. Enumerated value, validated against possible values
    present in the ``pyWavelet.wavelist()``. Current possible values (pywavelet version 0.4.0) (where an
    aditional number is needed, range of values is indicated in []):

    - haar
    - dmey
    - sym[2-20]
    - db[1-20]
    - coif[1-5]
    - bior[1.1, 1.3, 1.5, 2.2, 2.4, 2.6, 2.8, 3.1, 3.3, 3.5, 3.7, 3.9, 4.4, 5.5, 6.8]
    - rbio[1.1, 1.3, 1.5, 2.2, 2.4, 2.6, 2.8, 3.1, 3.3, 3.5, 3.7, 3.9, 4.4, 5.5, 6.8]

  Returned filter name reflects wavelet type:
  wavelet[level]-<decompositionName>

  N.B. only levels greater than the first level are entered into the name.

  :return: Yields each wavelet decomposition and final approximation, corresponding filter name and ``kwargs``
  """
  global logger

  logger.debug('Generating Wavelet images')

  approx, ret = _swt3(inputImage, kwargs.get('wavelet', 'coif1'), kwargs.get('level', 1), kwargs.get('start_level', 0))

  for idx, wl in enumerate(ret, start=1):
    for decompositionName, decompositionImage in wl.items():
      logger.info('Computing Wavelet %s', decompositionName)

      if idx == 1:
        inputImageName = 'wavelet-%s' % (decompositionName)
      else:
        inputImageName = 'wavelet%s-%s' % (idx, decompositionName)
      logger.debug('Yielding %s image', inputImageName)
      yield decompositionImage, inputImageName, kwargs

  if len(ret) == 1:
    inputImageName = 'wavelet-LLL'
  else:
    inputImageName = 'wavelet%s-LLL' % (len(ret))
  logger.debug('Yielding approximation (%s) image', inputImageName)
  yield approx, inputImageName, kwargs


def _swt3(inputImage, wavelet='coif1', level=1, start_level=0):
  matrix = sitk.GetArrayFromImage(inputImage)
  matrix = numpy.asarray(matrix)
  if matrix.ndim != 3:
    raise ValueError('Expected 3D data array')

  original_shape = matrix.shape
  adjusted_shape = tuple([dim + 1 if dim % 2 != 0 else dim for dim in original_shape])
  data = matrix.copy()
  data.resize(adjusted_shape, refcheck=False)

  if not isinstance(wavelet, pywt.Wavelet):
    wavelet = pywt.Wavelet(wavelet)

  for i in range(0, start_level):
    H, L = _decompose_i(data, wavelet)
    LH, LL = _decompose_j(L, wavelet)
    LLH, LLL = _decompose_k(LL, wavelet)

    data = LLL.copy()

  ret = []
  for i in range(start_level, start_level + level):
    H, L = _decompose_i(data, wavelet)

    HH, HL = _decompose_j(H, wavelet)
    LH, LL = _decompose_j(L, wavelet)

    HHH, HHL = _decompose_k(HH, wavelet)
    HLH, HLL = _decompose_k(HL, wavelet)
    LHH, LHL = _decompose_k(LH, wavelet)
    LLH, LLL = _decompose_k(LL, wavelet)

    data = LLL.copy()

    dec = {'HHH': HHH,
           'HHL': HHL,
           'HLH': HLH,
           'HLL': HLL,
           'LHH': LHH,
           'LHL': LHL,
           'LLH': LLH}
    for decName, decImage in six.iteritems(dec):
      decTemp = decImage.copy()
      decTemp = numpy.resize(decTemp, original_shape)
      sitkImage = sitk.GetImageFromArray(decTemp)
      sitkImage.CopyInformation(inputImage)
      dec[decName] = sitkImage

    ret.append(dec)

  data = numpy.resize(data, original_shape)
  approximation = sitk.GetImageFromArray(data)
  approximation.CopyInformation(inputImage)

  return approximation, ret


def _decompose_i(data, wavelet):
  # process in i:
  H, L = [], []
  i_arrays = chain.from_iterable(data)
  for i_array in i_arrays:
    cA, cD = pywt.swt(i_array, wavelet, level=1, start_level=0)[0]
    H.append(cD)
    L.append(cA)
  H = numpy.hstack(H).reshape(data.shape)
  L = numpy.hstack(L).reshape(data.shape)
  return H, L


def _decompose_j(data, wavelet):
  # process in j:
  s = data.shape
  H, L = [], []
  j_arrays = chain.from_iterable(numpy.transpose(data, (0, 2, 1)))
  for j_array in j_arrays:
    cA, cD = pywt.swt(j_array, wavelet, level=1, start_level=0)[0]
    H.append(cD)
    L.append(cA)
  H = numpy.hstack(H).reshape((s[0], s[2], s[1])).transpose((0, 2, 1))
  L = numpy.hstack(L).reshape((s[0], s[2], s[1])).transpose((0, 2, 1))
  return H, L


def _decompose_k(data, wavelet):
  # process in k:
  H, L = [], []
  k_arrays = chain.from_iterable(numpy.transpose(data, (2, 1, 0)))
  for k_array in k_arrays:
    cA, cD = pywt.swt(k_array, wavelet, level=1, start_level=0)[0]
    H.append(cD)
    L.append(cA)
  H = numpy.asarray([slice for slice in numpy.split(numpy.vstack(H), data.shape[2])]).T
  L = numpy.asarray([slice for slice in numpy.split(numpy.vstack(L), data.shape[2])]).T
  return H, L


def getSquareImage(inputImage, **kwargs):
  r"""
  Computes the square of the image intensities.

  Resulting values are rescaled on the range of the initial original image and negative intensities are made
  negative in resultant filtered image.

  :math:`f(x) = (cx)^2,\text{ where } c=\displaystyle\frac{1}{\sqrt{\max(x)}}`

  Where :math:`x` and :math:`f(x)` are the original and filtered intensity, respectively.

  :return: Yields square filtered image, 'square' and ``kwargs``
  """
  global logger

  im = sitk.GetArrayFromImage(inputImage)
  im = im.astype('float64')
  coeff = 1 / numpy.sqrt(numpy.max(im))
  im = (coeff * im) ** 2
  im = sitk.GetImageFromArray(im)
  im.CopyInformation(inputImage)

  logger.debug('Yielding square image')
  yield im, 'square', kwargs


def getSquareRootImage(inputImage, **kwargs):
  r"""
  Computes the square root of the absolute value of image intensities.

  Resulting values are rescaled on the range of the initial original image and negative intensities are made
  negative in resultant filtered image.

  :math:`f(x) = \left\{ {\begin{array}{lcl}
  \sqrt{cx} & \mbox{for} & x \ge 0 \\
  -\sqrt{-cx} & \mbox{for} & x < 0\end{array}} \right.,\text{ where } c=\max(x)`

  Where :math:`x` and :math:`f(x)` are the original and filtered intensity, respectively.

  :return: Yields square root filtered image, 'squareroot' and ``kwargs``
  """
  global logger

  im = sitk.GetArrayFromImage(inputImage)
  im = im.astype('float64')
  coeff = numpy.max(im)
  im[im > 0] = numpy.sqrt(im[im > 0] * coeff)
  im[im < 0] = - numpy.sqrt(-im[im < 0] * coeff)
  im = sitk.GetImageFromArray(im)
  im.CopyInformation(inputImage)

  logger.debug('Yielding squareroot image')
  yield im, 'squareroot', kwargs


def getLogarithmImage(inputImage, **kwargs):
  r"""
  Computes the logarithm of the absolute value of the original image + 1.

  Resulting values are rescaled on the range of the initial original image and negative intensities are made
  negative in resultant filtered image.

  :math:`f(x) = \left\{ {\begin{array}{lcl}
  c\log{(x + 1)} & \mbox{for} & x \ge 0 \\
  -c\log{(-x + 1)} & \mbox{for} & x < 0\end{array}} \right. \text{, where } c=\left\{ {\begin{array}{lcl}
  \frac{\max(x)}{\log(\max(x) + 1)} & if & \max(x) \geq 0 \\
  \frac{\max(x)}{-\log(-\max(x) - 1)} & if & \max(x) < 0 \end{array}} \right.`

  Where :math:`x` and :math:`f(x)` are the original and filtered intensity, respectively.

  :return: Yields logarithm filtered image, 'logarithm' and ``kwargs``
  """
  global logger

  im = sitk.GetArrayFromImage(inputImage)
  im = im.astype('float64')
  im_max = numpy.max(im)
  im[im > 0] = numpy.log(im[im > 0] + 1)
  im[im < 0] = - numpy.log(- (im[im < 0] - 1))
  im = im * (im_max / numpy.max(im))
  im = sitk.GetImageFromArray(im)
  im.CopyInformation(inputImage)

  logger.debug('Yielding logarithm image')
  yield im, 'logarithm', kwargs


def getExponentialImage(inputImage, **kwargs):
  r"""
  Computes the exponential of the original image.

  Resulting values are rescaled on the range of the initial original image.

  :math:`f(x) = e^{cx},\text{ where } c=\displaystyle\frac{\log(\max(x))}{\max(x)}`

  Where :math:`x` and :math:`f(x)` are the original and filtered intensity, respectively.

  :return: Yields exponential filtered image, 'exponential' and ``kwargs``
  """
  global logger

  im = sitk.GetArrayFromImage(inputImage)
  im = im.astype('float64')
  coeff = numpy.log(numpy.max(im)) / numpy.max(im)
  im = numpy.exp(coeff * im)
  im = sitk.GetImageFromArray(im)
  im.CopyInformation(inputImage)

  logger.debug('Yielding exponential image')
  yield im, 'exponential', kwargs
