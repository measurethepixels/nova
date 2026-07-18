/*
 * Image Plate Solver
 *
 * Plate solving of astronomical images.
 *
 * Copyright (C) 2012-2024, Andres del Pozo
 * Copyright (C) 2019-2024, Juan Conejero (PTeam)
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * Coordinate Systems
 *
 * (I) Image Coordinates
 *    Image pixel coordinates on the PixInsight platform.
 *    - Grows from left to right and from top to bottom.
 *    - The origin is at the top left corner of the image. The center of the
 *      top left pixel has image coordinates (0.5,0.5).
 *
 * (G) Gnomonic Projected Space
 *    Projected space resulting of projecting celestial coordinates using a
 *    Gnomonic projection.
 *    - Coincides with the World Intermediate Coordinates of WCS.
 *    - Grows from right to left and from bottom to top.
 *    - The center of the image has coordinates (0,0).
 *
 * (F) FITS WCS Coordinates
 *    Pixels of the image using WCS conventions.
 *    - http://fits.gsfc.nasa.gov/fits_wcs.html
 *      "Representations of World Coordinates in FITS" (Sections 2.1.4 and 5.1)
 *      "Representations of celestial coordinates in FITS" (Section 5, p. 1085)
 *    - Grows from left to right and from bottom to top.
 *    - The center of the bottom left pixel has the coordinates (1,1).
 */

/* beautify ignore:start */

#feature-id    ImageSolver : Astrometry > ImageSolver

#feature-icon  @script_icons_dir/ImageSolver.svg

#feature-info  A script for the calculation of astrometric solutions.<br/>\
               <br/>\
               Copyright &copy; 2012-2024 Andr&eacute;s del Pozo<br/>\
               Copyright &copy; 2019-2024 Juan Conejero (PTeam)

#ifndef USE_SOLVER_LIBRARY
// Global control variable for PCL invocation.
var __PJSR_AdpImageSolver_SuccessCount = 0;
#endif

if ( CoreApplication === undefined ||
     CoreApplication.versionRevision === undefined ||
     CoreApplication.versionMajor*1e11
   + CoreApplication.versionMinor*1e8
   + CoreApplication.versionRelease*1e5
   + CoreApplication.versionRevision*1e2 < 100900000000 )
{
   throw new Error( "This script requires PixInsight core version 1.9.0 or higher." );
}

#define __PJSR_USE_STAR_DETECTOR_V2

#include <pjsr/BRQuadTree.jsh>
#include <pjsr/ColorSpace.jsh>
#include <pjsr/DataType.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/LinearTransformation.jsh>
#include <pjsr/NumericControl.jsh>
#include <pjsr/RBFType.jsh>
#include <pjsr/SectionBar.jsh>
#include <pjsr/Sizer.jsh>
#include <pjsr/StarDetector.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdCursor.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/UndoFlag.jsh>

#define SOLVERVERSION "6.3.1"

#ifndef USE_SOLVER_LIBRARY

#define TITLE           "Image Solver"
#define SETTINGS_MODULE "SOLVER"
#define STAR_CSV_FILE   (File.systemTempDirectory + format( "/stars-%03d.csv", CoreApplication.instance ))

#include "WCSmetadata.jsh"
#include "AstronomicalCatalogs.jsh"
#include "SearchCoordinatesDialog.js"
#include "CatalogDownloader.js"

#endif // !USE_SOLVER_LIBRARY

#define SETTINGS_MODULE_SCRIPT "SOLVER"

/* beautify ignore:end */

/*
 * Enumerations
 */
function CatalogMode() {}
CatalogMode.prototype.LocalText = 0;
CatalogMode.prototype.Online = 1;
CatalogMode.prototype.Automatic = 2;
CatalogMode.prototype.LocalXPSDServer = 3;

function IntersectionMode() {}
IntersectionMode.prototype.Never = 0;
IntersectionMode.prototype.Automatic = 1;
IntersectionMode.prototype.Always = 2;

/*
 * SolverConfiguration: Configuration information of the ImageSolver engine.
 */
function SolverConfiguration( module )
{
   this.__base__ = ObjectWithSettings;
   this.__base__(
      module,
      "solver",
      new Array(
         [ "version", DataType_UCString ],
         [ "magnitude", DataType_Float ],
         [ "autoMagnitude", DataType_Boolean ],
         [ "databasePath", DataType_UCString ],
         [ "generateErrorImg", DataType_Boolean ],
         [ "structureLayers", DataType_UInt8 ],
         [ "minStructureSize", DataType_UInt8 ],
         [ "hotPixelFilterRadius", DataType_UInt8 ],
         [ "noiseReductionFilterRadius", DataType_UInt8 ],
         [ "sensitivity", DataType_Double ],
         [ "peakResponse", DataType_Double ],
         [ "brightThreshold", DataType_Double ],
         [ "maxStarDistortion", DataType_Double ],
         [ "autoPSF", DataType_Boolean ],
         [ "catalogMode", DataType_UInt8 ],
         [ "vizierServer", DataType_UCString ],
         [ "showStars", DataType_Boolean ],
         [ "showStarMatches", DataType_Boolean ],
         [ "showSimplifiedSurfaces", DataType_Boolean ],
         [ "showDistortion", DataType_Boolean ],
         [ "generateDistortModel", DataType_Boolean ],
         [ "catalog", DataType_UCString ],
         [ "distortionCorrection", DataType_Boolean ],
         [ "rbfType", DataType_Int32 ],
         [ "maxSplinePoints", DataType_Int32 ],
         [ "splineOrder", DataType_UInt8 ],
         [ "splineSmoothing", DataType_Float ],
         [ "enableSimplifier", DataType_Boolean ],
         [ "simplifierRejectFraction", DataType_Float ],
         [ "outlierDetectionRadius", DataType_Int32 ],
         [ "outlierDetectionMinThreshold", DataType_Float ],
         [ "outlierDetectionSigma", DataType_Float ],
         [ "useActive", DataType_Boolean ],
         [ "outSuffix", DataType_UCString ],
         [ "files", Ext_DataType_StringArray ],
         [ "projection", DataType_UInt8 ],
         [ "projectionOriginMode", DataType_UInt8 ],
         [ "restrictToHQStars", DataType_Boolean ],
         [ "intersectionMode", DataType_UInt8 ],
         [ "tryApparentCoordinates", DataType_Boolean ],
         [ "tryExhaustiveInitialAlignment", DataType_Boolean ]
      )
   );

   this.version = SOLVERVERSION;
   this.useActive = true;
   this.files = [];
   this.catalogMode = CatalogMode.prototype.Automatic;
   this.availableCatalogs = [
      new PPMXLCatalog(),
      new TychoCatalog(),
      new HR_Catalog(),
      new GaiaDR2_Catalog()
   ];
   this.availableXPSDServers = [
      new GaiaDR3XPSDCatalog(),
      new GaiaEDR3XPSDCatalog(),
      new GaiaDR2XPSDCatalog()
   ];
   this.vizierServer = "https://vizier.cds.unistra.fr/";
   this.magnitude = 12;
   this.maxIterations = 100;
   this.structureLayers = 5;
   this.minStructureSize = 0;
   this.hotPixelFilterRadius = 1;
   this.noiseReductionFilterRadius = 0;
   this.sensitivity = 0.5;
   this.peakResponse = 0.5;
   this.brightThreshold = 3.0;
   this.maxStarDistortion = 0.6;
   this.autoPSF = false;
   this.generateErrorImg = false;
   this.showStars = false;
   this.catalog = "PPMXL";
   this.autoMagnitude = true;
   this.showStarMatches = false;
   this.showSimplifiedSurfaces = false;
   this.showDistortion = false;
   this.distortionCorrection = true;
   this.rbfType = WCS_DEFAULT_RBF_TYPE;
   this.maxSplinePoints = WCS_DEFAULT_MAX_SPLINE_POINTS;
   this.splineOrder = 2;
   this.splineSmoothing = 0.005;
   this.enableSimplifier = true;
   this.simplifierRejectFraction = 0.10;
   this.outlierDetectionRadius = 160;
   this.outlierDetectionMinThreshold = 4.0;
   this.outlierDetectionSigma = 5.0;
   this.generateDistortModel = false;
   this.outSuffix = "_ast";
   this.projection = 0;
   this.projectionOriginMode = 0;
   this.restrictToHQStars = false;
   this.intersectionMode = IntersectionMode.prototype.Automatic;
   this.tryApparentCoordinates = true;
   this.tryExhaustiveInitialAlignment = false;

   this.ResetSettings = function()
   {
      Settings.remove( SETTINGS_MODULE );
   };
}

SolverConfiguration.prototype = new ObjectWithSettings;


// ----------------------------------------------------------------------------

/*
 * ImageSolver: Implementation of the plate solving algorithm.
 */
function ImageSolver()
{
   let error;
   this.solverCfg = new SolverConfiguration( SETTINGS_MODULE_SCRIPT );
   this.metadata = new ImageMetadata( SETTINGS_MODULE_SCRIPT );

   /*
    * Initializes the image solver. If the parameter prioritizeSettings is
    * defined and is true, the solver will use the values stored in preferences
    * instead of the values obtained from the image.
    */
   this.Init = function( window, prioritizeSettings )
   {
      function compareVersions( v1, v2 )
      {
         let a1 = v1.split( '.' );
         let a2 = v2.split( '.' );
         let n = Math.min( a1.length, a2.length );
         if ( n < 2 )
            return true; // invalid -> v1 < v2
         for ( let i = 0; i < n; ++i )
         {
            if ( a1[i] < a2[i] )
               return true; // v1 < v2
            if ( a1[i] > a2[i] )
               return false; // v1 > v2
         }
         return false; // v1 == v2
      }

      this.solverCfg.LoadSettings();
      this.solverCfg.LoadParameters();

      /*
       * Be compatible with versions < 4.2.4, where some catalog names had
       * leading and trailing spaces.
       */
      this.solverCfg.catalog = this.solverCfg.catalog.trim();

      /*
       * Version 5.5.0 introduces a new surface simplification algorithm for
       * generation of thin plate splines, where an optimal simplifier
       * tolerance in pixels is found automatically. This changes the meaning
       * of the simplifierTolerance parameter. For sanity, we'll reset all
       * surface simplification parameters if we detect an older version in the
       * previous script execution.
       */
      if ( compareVersions( this.solverCfg.version, "5.5.0" ) )
      {
         //this.solverCfg.splineSmoothing = 0.015; // see v6.0 below
         this.solverCfg.enableSimplifier = true;
         //this.solverCfg.simplifierTolerance = 0.05; // see v6.0 below
         this.solverCfg.simplifierRejectFraction = 0.10;
      }

      /*
       * Since version 5.6.3 we use the new StarDetector engine V2 introduced
       * in core 1.8.9-1. We must reset some critical star detection parameters
       * to their new default values.
       */
      if ( compareVersions( this.solverCfg.version, "5.6.3" ) )
      {
         this.solverCfg.sensitivity = 0.5;
         this.solverCfg.peakResponse = 0.5;
         this.solverCfg.maxStarDistortion = 0.6;
      }

      /*
       * Since version 6.0, as a result of the new robust star matching
       * algorithm:
       *
       * - Distortion correction is now enabled by default.
       * - The default spline smoothing parameter has been reduced to 0.010.
       * - Simplification tolerances are calculated adaptively, hence the
       *   simplifierTolerance parameter has been removed.
       */
      if ( compareVersions( this.solverCfg.version, "6.0" ) )
      {
         this.solverCfg.distortionCorrection = true;
         //this.splineSmoothing = 0.010; // see v6.1 below
      }

      /*
       * Since version 6.1, the default spline smoothing parameter value has
       * been reduced to 0.005; previously it was 0.01. We can afford this
       * precision increment consistently thanks to the improvements
       * implemented in our distortion modeling algorithm.
       */
      if ( compareVersions( this.solverCfg.version, "6.1" ) )
      {
         this.splineSmoothing = 0.005;
      }

      /*
       * Since version 6.2, the default output filename suffix is "_ast"
       * (previously it was "_WCS").
       */
      if ( compareVersions( this.solverCfg.version, "6.2" ) )
      {
         this.outSuffix = "_ast";
      }

      if ( prioritizeSettings )
         if ( window && window.isWindow )
            this.metadata.ExtractMetadata( window );

      this.metadata.LoadSettings();
      this.metadata.LoadParameters();

      if ( !prioritizeSettings )
         if ( window && window.isWindow )
            this.metadata.ExtractMetadata( window );

      this.metadata.ensureValidReferenceSystemForSolution();
   };

   this.InitialAlignment = function( window, numStars )
   {
      let SA = new StarAlignment;
      SA.referenceImage = STAR_CSV_FILE;
      SA.referenceIsFile = true;
      SA.mode = StarAlignment.prototype.OutputMatrix;
      SA.writeKeywords = false;
      SA.structureLayers = this.solverCfg.structureLayers;
      SA.minStructureSize = this.solverCfg.minStructureSize;
      SA.hotPixelFilterRadius = this.solverCfg.hotPixelFilterRadius;
      SA.noiseReductionFilterRadius = this.solverCfg.noiseReductionFilterRadius;
      SA.sensitivity = this.solverCfg.sensitivity;
      SA.peakResponse = this.solverCfg.peakResponse;
      SA.brightThreshold = this.solverCfg.brightThreshold;
      SA.maxStarDistortion = this.solverCfg.maxStarDistortion;
      SA.allowClusteredSources = true; // because we want it to match as many stars as possible at this stage
      SA.ransacTolerance = 2;
      SA.ransacMaxIterations = 5000;
      SA.ransacMaximizeInliers = 1;
      SA.ransacMaximizeOverlapping = 0;
      SA.ransacMaximizeRegularity = 0;
      SA.ransacMinimizeError = 0;
      SA.useTriangles = false; // for robustness, always use polygonal descriptors
      SA.polygonSides = 7;
      SA.maxStars = (numStars <= 2000) ?                      // exhaustive star matching:
               0 :                                            // - always enabled for small star sets
               Math.min( 2000, Math.trunc( 1.25*numStars ) ); // - enable on user request for large sets
      SA.descriptorsPerStar = 100;
      SA.restrictToPreviews = false;

      switch ( this.solverCfg.intersectionMode )
      {
      case IntersectionMode.prototype.Never:
         SA.intersection = StarAlignment.prototype.NoIntersection;
         break;
      default:
      case IntersectionMode.prototype.Automatic:
         {
            let w = window.mainView.image.width;
            let h = window.mainView.image.height;
            SA.intersection = (Math.max( w, h ) / Math.min( w, h ) >= 2.0) ?
               StarAlignment.prototype.Always : StarAlignment.prototype.NoIntersection;
         }
         break;
      case IntersectionMode.prototype.Always:
         SA.intersection = StarAlignment.prototype.Always;
         break;
      }

      if ( !SA.executeOn( window.currentView, false/*swapFile*/ ) )
         return null;

      let numPairs = Math.min( SA.outputData[0][2], 4000 );
      let pairs = {
         pS: new Array( numPairs ),
         pI: new Array( numPairs )
      };
      for ( let i = 0; i < numPairs; ++i )
      {
         pairs.pS[i] = new Point( SA.outputData[0][29][i],
                                  SA.outputData[0][30][i] );
         pairs.pI[i] = new Point( SA.outputData[0][31][i] + 0.5,
                                  SA.outputData[0][32][i] + 0.5 );
      }
      return pairs;
   };

   this.GenerateTemplate = function( metadata, templateGeom, mirrored )
   {
      if ( mirrored === undefined )
         mirrored = false;

      if ( this.solverCfg.catalogMode == CatalogMode.prototype.LocalText )
      {
         this.catalog = new CustomCatalog( this.solverCfg.databasePath );
      }
      else
      {
         this.catalog = __catalogRegister__.GetCatalog( this.catalogName );
         this.catalog.magMax = this.limitMagnitude;
         this.catalog.restrictToHQStars = this.solverCfg.restrictToHQStars;
      }

      this.catalog.Load( metadata, this.solverCfg.vizierServer );
      if ( this.catalog.objects == null )
         throw "Catalog error: " + this.catalogName;

      let ref_G_S = templateGeom.ref_S_G.inverse();

      let file = File.createFileForWriting( STAR_CSV_FILE );
      file.outTextLn( templateGeom.width + "," + templateGeom.height );
      let elements = this.catalog.objects;
      let numStars = 0;
      let clipRectS = templateGeom.clipRectS || new Rect( 0, 0, templateGeom.width, templateGeom.height );

      for ( let i = 0; i < elements.length; ++i )
         if ( elements[i] )
         {
            let flux = (elements[i].magnitude == null) ? 0 : Math.pow( 2.512, -1.5 - elements[i].magnitude );
            let pos_G = templateGeom.projection.Direct( elements[i].posRD );
            if ( pos_G )
            {
               let pos_S = ref_G_S.apply( pos_G );
               if ( pos_S.x > clipRectS.left
                 && pos_S.x < clipRectS.right
                 && pos_S.y > clipRectS.top
                 && pos_S.y < clipRectS.bottom )
               {
                  let x = pos_S.x;
                  if ( mirrored )
                     x = Math.abs( x - templateGeom.width );
                  file.outTextLn( format( "%.4f,%.4f,%.3e", x, pos_S.y, flux ) );
                  numStars++;
               }
            }
         }

      file.close();
      if ( numStars < 3 ) // 3 stars is the minimum required for a rigid transformation
         throw "Found too few stars. The limit magnitude could be too low, or the catalog server could be malfunctioning.";

      return numStars;
   };

   this.DoIterationSA = function( window, metadata )
   {
      try
      {
         /*
          * Render a star field around the original coordinates.
          */
         let templateSize = Math.min( metadata.width, metadata.height ); // Math.max( metadata.width, metadata.height );
         let templateGeom = {
            ref_S_G: new Matrix( -metadata.resolution,  0,                   metadata.resolution * templateSize/2,
                                  0,                   -metadata.resolution, metadata.resolution * templateSize/2,
                                  0,                    0,                   1 ),
            projection: ProjectionFactory( this.solverCfg, metadata.ra, metadata.dec ),
            width: templateSize,
            height: templateSize,
            clipRectS: null
         };

         /*
          * Initial alignment of the reference catalog positions with the stars
          * detected in the image.
          *
          * Step 0: Use ICRS coordinates.
          * Step 1: Use apparent coordinates if requested.
          */
         let pairs = null;
         for ( let step = 0; step < 2; ++step )
         {
            if ( step == 1 )
            {
               /*
                * Some image acquisition applications store apparent or 'of the
                * date' coordinates in image metadata without providing the
                * required metadata items to let us know. If the corresponding
                * option is enabled, make a second attempt assuming that the
                * center coordinates are apparent.
                */
               console.noteln( "<end><cbr><br>* Previous attempts failed - trying again assuming apparent coordinates." );
               metadata.convertRADecFromApparentToAstrometric();
               metadata.mightBeApparent = false;
               templateGeom.projection = ProjectionFactory( this.solverCfg, metadata.ra, metadata.dec );
            }

            /*
             * Substep 0: Use optimal star matching.
             * Substep 1: Use exhaustive star matching if requested.
             */
            for ( let substep = 0; substep < 2; ++substep )
            {
               if ( substep == 1 )
                  console.noteln( "<end><cbr><br>* Previous attempts failed - trying again using exhaustive star matching." );

               /*
                * Perform the initial image registration using a projective
                * transformation.
                */
               let numStars = this.GenerateTemplate( metadata, templateGeom );
               pairs = this.InitialAlignment( window, (substep == 0) ? numStars : 0 );
               if ( pairs !== null )
                  break;

               /*
                * If failed, the most frequent reason is a mirrored image,
                * where polygonal star matching descriptors don't work. Try
                * again with all reference star positions mirrored
                * horizontally.
                * N.B.: This method is much better than using triangle
                * similarity to register the mirrored image. The robustness and
                * flexibility of polygonal descriptors are essential for the
                * initial alignment task.
                */
               console.noteln( "<end><cbr><br>* Previous attempt failed - trying with mirrored reference coordinates." );
               numStars = this.GenerateTemplate( metadata, templateGeom, true/*mirrored*/ );
               pairs = this.InitialAlignment( window, (substep == 0) ? numStars : 0 );
               if ( pairs !== null )
               {
                  // De-mirror reference positions, so the mirrored alignment
                  // process is transparent.
                  for ( let i = 0; i < pairs.pS.length; ++i )
                     pairs.pS[i].x = Math.abs( pairs.pS[i].x - templateGeom.width );
                  break;
               }

               if ( !this.solverCfg.tryExhaustiveInitialAlignment || numStars <= 2000 )
                  break;
            }

            if ( pairs !== null )
            {
               if ( step == 1 )
                  console.warningln( "<end><cbr><br>** Warning: The image provides apparent or 'of the date' coordinates in " +
                     "image metadata but does not include the appropriate Observation:CelestialReferenceSystem XISF property " +
                     "or RADESYS FITS keyword. We suggest you inform the authors of your image acquisition application about " +
                     "this error, which they should fix." );
               break;
            }

            if ( !metadata.mightBeApparent || !this.solverCfg.tryApparentCoordinates )
               break;
         }

         if ( pairs === null )
         {
            if ( !console.isAborted )
            {
               console.criticalln( "<end><cbr><br>*** Error: The image could not be aligned with the reference star field." );
               console.writeln(
                  "<html>" +
                     "<p><strong>Please check the following items:</strong></p>" +
                     "<ul>" +
                        "<li>The initial center coordinates should be inside the image.</li>" +
                        "<li>The initial image resolution should be within a factor of 2 from the correct value.</li>" +
                        "<li>If you use an online star catalog through the VizieR service, consider using " +
                           "the Gaia DR3 catalog with local XPSD databases instead.</li>" +
                        "<li>If the image has extreme noise levels, bad tracking, or is poorly focused, you may " +
                           "need to adjust some star detection parameters.</li>" +
                     "</ul>" +
                  "</html>" );
            }
            return null;
         }

         /*
          * Adjust to a projection with the origin at the center of the image.
          */
         let pG = pairs.pS.map( p => templateGeom.ref_S_G.apply( p ) );
         let ref_S_G = Math.homography( pairs.pI, pG );
         let centerRD = templateGeom.projection.Inverse( ref_S_G.apply( new Point( metadata.width/2, metadata.height/2 ) ) );
         let newProjection = ProjectionFactory( this.solverCfg, centerRD.x, centerRD.y );
         pairs.pG = pG.map( p => newProjection.Direct( templateGeom.projection.Inverse( p ) ) );
         templateGeom.projection = newProjection;

         /*
          * Initialize a new metadata structure appropriate for the selected
          * working mode.
          */
         let newMetadata = metadata.Clone();
         newMetadata.projection = templateGeom.projection;

         if ( this.solverCfg.distortionCorrection )
         {
            // Using surface splines.
            newMetadata.ref_I_G_linear = Math.homography( pairs.pI, pairs.pG );

            newMetadata.ref_I_G = new ReferSpline( pairs.pI, pairs.pG,
                                                   this.solverCfg.rbfType,
                                                   this.solverCfg.maxSplinePoints,
                                                   this.solverCfg.splineOrder,
                                                   this.solverCfg.splineSmoothing,
                                                   this.solverCfg.enableSimplifier,
                                                   this.solverCfg.simplifierRejectFraction );
            newMetadata.ref_G_I = newMetadata.ref_I_G.inverse;

            processEvents();

            newMetadata.controlPoints = {
               pI: pairs.pI,
               pG: pairs.pG
            };
         }
         else
         {
            // Using a linear solution.
            newMetadata.ref_I_G = Math.homography( pairs.pI, pairs.pG );
            newMetadata.ref_I_G_linear = newMetadata.ref_I_G;
            newMetadata.ref_G_I = newMetadata.ref_I_G.inverse();
            newMetadata.controlPoints = null;
         }

         /*
          * Find the celestial coordinates (RD) of the center of the original
          * image. First transform from I to G and then unproject the native
          * projection coordinates (G) to celestial (RD).
          */
         let centerI = new Point( metadata.width/2, metadata.height/2 );
         let centerG = newMetadata.ref_I_G.apply( centerI );
         centerRD = newMetadata.projection.Inverse( centerG );
         while ( centerRD.x < 0 )
            centerRD.x += 360;
         while ( centerRD.x >= 360 )
            centerRD.x -= 360;
         newMetadata.ra = centerRD.x;
         newMetadata.dec = centerRD.y;
         let ref = newMetadata.ref_I_G_linear;
         let resx = Math.sqrt( ref.at( 0, 0 ) * ref.at( 0, 0 ) + ref.at( 0, 1 ) * ref.at( 0, 1 ) );
         let resy = Math.sqrt( ref.at( 1, 0 ) * ref.at( 1, 0 ) + ref.at( 1, 1 ) * ref.at( 1, 1 ) );
         newMetadata.resolution = (resx + resy)/2;
         newMetadata.focal = newMetadata.FocalFromResolution( newMetadata.resolution );
         newMetadata.useFocal = false;

         return newMetadata;
      }
      catch ( ex )
      {
         if ( !console.isAborted )
            if ( ex.length === undefined || ex.length > 0 )
               console.criticalln( "<end><cbr>*** Error: ", ex.toString() );
         return null;
      }
      finally
      {
         try
         {
            if ( File.exists( STAR_CSV_FILE ) )
               File.remove( STAR_CSV_FILE );
         }
         catch ( x )
         {
            // Propagate no further filesystem exceptions here.
         }
      }
   };

   this.MatchStars = function( window, predictedCoords )
   {
      /*
       * Putative point matches by proximity search.
       */
      let actualCoords = new Array( predictedCoords.length );
      for ( let i = 0; i < predictedCoords.length; ++i )
      {
         let p = predictedCoords[i];
         if ( p )
         {
            let s = this.starTree.search( { x0: p.x - this.psfSearchRadius,
                                            y0: p.y - this.psfSearchRadius,
                                            x1: p.x + this.psfSearchRadius,
                                            y1: p.y + this.psfSearchRadius } );
            if ( s.length > 0 )
            {
               let j = 0;
               if ( s.length > 1 )
               {
                  let star = this.starTree.objects[s[0]];
                  let dx = star.x - p.x;
                  let dy = star.y - p.y;
                  let d2 = dx*dx + dy*dy;
                  for ( let i = 1; i < s.length; ++i )
                  {
                     let star = this.starTree.objects[s[i]];
                     let dx = star.x - p.x;
                     let dy = star.y - p.y;
                     let d2i = dx*dx + dy*dy;
                     if ( d2i < d2 )
                     {
                        j = i;
                        d2 = d2i;
                     }
                  }
               }
               let star = this.starTree.objects[s[j]];
               actualCoords[i] = new Point( star.x, star.y );
            }
         }
      }

      if ( !this.solverCfg.distortionCorrection )
         return { matchedPoints: actualCoords, meanSparsity: 0, sigmaSparsity: 0, rejectionThreshold: 0 };

      /*
       * Adaptive spline outlier rejection based on local sparsity estimation.
       * In this context, outliers are wrongly extrapolated points that can
       * prevent modeling non-convex surfaces and regions of strongly varying
       * distortion by stalling surface spline generation in subsequent
       * iterations.
       */
      let P = [];
      for ( let i = 0; i < actualCoords.length; ++i )
      {
         let p = actualCoords[i];
         if ( p )
            P.push( {
               x: p.x, y: p.y,
               rect: {
                  x0: p.x-0.5, y0: p.y-0.5,
                  x1: p.x+0.5, y1: p.y+0.5
               },
               idx: i
            } );
      }

      // ### N.B. This loop is a performance bottleneck.
      // Reason: multiple quadtree search operations on a large rectangular
      // region (typically, outlierDetectionRadius = 160 px)
      let T = new BRQuadTree( P, 256/*bucketSize*/ );
      let S = new Float32Array( P.length );
      for ( let i = 0; i < P.length; ++i )
      {
         let p = P[i];
         let r = { x0: p.x - this.solverCfg.outlierDetectionRadius,
                   y0: p.y - this.solverCfg.outlierDetectionRadius,
                   x1: p.x + this.solverCfg.outlierDetectionRadius,
                   y1: p.y + this.solverCfg.outlierDetectionRadius };
         S[i] = this.starTree.count( r ) / T.count( r );
      }

      let m = Math.median( S );
      let s = 1.1926*Math.Sn( S );
      let d = Math.max( this.solverCfg.outlierDetectionMinThreshold,
                        m + this.solverCfg.outlierDetectionSigma*s );
      for ( let i = 0; i < P.length; ++i )
         if ( S[i] > d )
            P[i] = null; // outlier removed

      /*
       * Output coordinates.
       */
      let Q = new Array( predictedCoords.length );
      for ( let i = 0; i < P.length; ++i )
      {
         let p = P[i];
         if ( p )
            Q[p.idx] = actualCoords[p.idx];
      }
      return { matchedPoints: Q, meanSparsity: m, sigmaSparsity: s, rejectionThreshold: d };
   };

   this.DrawErrors = function( targetWindow, metadata, stars )
   {
      if ( !stars )
         return;
      console.writeln( "<end><cbr>Generating error map..." );

      let bmp = new Bitmap( metadata.width, metadata.height );

      // Copy the source image to the error image
      let imageOrg = targetWindow.mainView.image;
      let tmpW = new ImageWindow( metadata.width, metadata.height, imageOrg.numberOfChannels,
                                  targetWindow.bitsPerSample, targetWindow.isFloatSample, imageOrg.isColor,
                                  targetWindow.mainView.id + "_errors" );
      tmpW.mainView.beginProcess( UndoFlag_NoSwapFile );
      tmpW.mainView.image.apply( imageOrg );
      ApplySTF( tmpW.mainView, targetWindow.mainView.stf );
      tmpW.mainView.endProcess();
      bmp.assign( tmpW.mainView.image.render() );
      tmpW.forceClose();

      let g = new VectorGraphics( bmp );
      g.antialiasing = true;
      let linePen = new Pen( 0xffff4040, 1 );
      let starPen = new Pen( 0xff40ff40, 1 );
      let badStarPen = new Pen( 0xffff4040, 1 );
      for ( let i = 0; i < stars.actualCoords.length; ++i )
      {
         let predicted = metadata.Convert_RD_I( stars.starCoords[i] );
         if ( predicted )
         {
            if ( stars.actualCoords[i] )
            {
               let arrow = new Point( predicted.x + ( stars.actualCoords[i].x - predicted.x ) * 1,
                  predicted.y + ( stars.actualCoords[i].y - predicted.y ) * 1 );
               g.pen = linePen;
               g.drawLine( predicted, arrow );
               g.pen = starPen;
            }
            else
               g.pen = badStarPen;

            g.drawLine( predicted.x - 10, predicted.y, predicted.x - 5, predicted.y );
            g.drawLine( predicted.x + 10, predicted.y, predicted.x + 5, predicted.y );
            g.drawLine( predicted.x, predicted.y - 10, predicted.x, predicted.y - 5 );
            g.drawLine( predicted.x, predicted.y + 10, predicted.x, predicted.y + 5 );
         }
      }
      g.end();

      let errW = new ImageWindow( metadata.width, metadata.height,
                                  3/*channels*/, 8/*bits*/, false/*float*/, true/*color*/,
                                  targetWindow.mainView.id + "_errors" );
      errW.mainView.beginProcess( UndoFlag_NoSwapFile );
      errW.mainView.image.blend( bmp );
      errW.keywords = targetWindow.keywords;
      errW.mainView.endProcess();
      errW.show();
   };

   this.DrawStars = function( targetWindow, metadata, S, id )
   {
      let bmp = new Bitmap( metadata.width, metadata.height );
      bmp.fill( 0xffffffff );
      let g = new VectorGraphics( bmp );
      g.antialiasing = true;
      let linePen = new Pen( 0xff000000, 2 );
      g.pen = linePen;
      for ( let i = 0; i < S.length; ++i )
         if ( S[i] )
         {
            let p = S[i];
            g.drawLine( p.x - 10, p.y, p.x + 10, p.y );
            g.drawLine( p.x, p.y - 10, p.x, p.y + 10 );
         }
      g.end();

      if ( id === undefined || id.length == 0 )
         id = targetWindow.mainView.id + "_stars";
      let window = new ImageWindow( metadata.width, metadata.height,
                           1/*channels*/, 8/*bits*/, false/*float*/, false/*color*/, id );
      window.mainView.beginProcess( UndoFlag_NoSwapFile );
      window.mainView.image.blend( bmp );
      window.mainView.endProcess();
      window.show();
   };

   this.DrawSimplifiedSurface = function( targetWindow, metadata, S, suffix )
   {
      let bmp = new Bitmap( metadata.width, metadata.height );
      bmp.fill( 0xffffffff );
      let g = new VectorGraphics( bmp );
      g.antialiasing = true;
      let linePen = new Pen( 0xff000000, 2 );
      g.pen = linePen;
      for ( let i = 0; i < S.length; ++i )
      {
         let p = S[i];
         g.drawLine( p.x - 10, p.y, p.x + 10, p.y );
         g.drawLine( p.x, p.y - 10, p.x, p.y + 10 );
      }
      g.end();

      let window = new ImageWindow( metadata.width, metadata.height,
                           1/*channels*/, 8/*bits*/, false/*float*/, false/*color*/,
                           targetWindow.mainView.id + suffix + "_simplified" );
      window.mainView.beginProcess( UndoFlag_NoSwapFile );
      window.mainView.image.blend( bmp );
      window.mainView.endProcess();
      window.show();
   };

   this.DrawSimplifiedSurfaces = function( targetWindow, metadata )
   {
      console.writeln( "<end><cbr>Generating simplified surface maps..." );

      if ( !metadata.ref_I_G.simpleX || !metadata.ref_I_G.simpleY )
      {
         console.warningln( "** Warning: Internal error: No simplified surfaces available." );
         return;
      }

      this.DrawSimplifiedSurface( targetWindow, metadata, metadata.ref_I_G.simpleX, "_I_G_X" );
      this.DrawSimplifiedSurface( targetWindow, metadata, metadata.ref_I_G.simpleY, "_I_G_Y" );
      this.DrawSimplifiedSurface( targetWindow, metadata, metadata.ref_G_I.applyToPoints( metadata.ref_G_I.simpleX ), "_G_I_X" );
      this.DrawSimplifiedSurface( targetWindow, metadata, metadata.ref_G_I.applyToPoints( metadata.ref_G_I.simpleY ), "_G_I_Y" );
   };

   this.DrawDistortions = function( targetWindow, metadata )
   {
      console.writeln( "<end><cbr>Generating distortion map..." );

      let ref_I_G_linear = metadata.ref_I_G_linear;
      if ( metadata.controlPoints )
      {
         let centerI = new Point( metadata.width / 2, metadata.height / 2 );
         let centerG = metadata.ref_I_G.apply( centerI );
         ref_I_G_linear = MultipleLinearRegressionHelmert( metadata.controlPoints.pI, metadata.controlPoints.pG, centerI, centerG );
      }

      let cellSize = Math.max( metadata.width, metadata.height )
                   / Math.trunc( Math.max( metadata.width, metadata.height )/64 );
      let bmp = new Bitmap( metadata.width, metadata.height );
      bmp.fill( 0xffffffff ); // solid white
      let g = new VectorGraphics( bmp );
      g.antialiasing = true;

      g.pen = new Pen( 0xff800000, 2 ); // dark red
      for ( let y = 0; y < metadata.height; y += cellSize )
         for ( let x = 0; x < metadata.width; x += cellSize )
         {
            let posLinearI = new Point( x + cellSize / 2, y + cellSize / 2 );
            let posG = ref_I_G_linear.apply( posLinearI );
            let posDistortI = metadata.ref_G_I.apply( posG );
            if ( !posDistortI )
               continue;
            let arrow = new Point( posDistortI.x + (posLinearI.x - posDistortI.x),
                                   posDistortI.y + (posLinearI.y - posDistortI.y) );
            g.drawLine( posDistortI, arrow );
            g.drawCircle( posDistortI, 1 );
         }
      g.pen = new Pen( 0xff000000, 2 ); // black
      for ( let y = 0; y - cellSize <= metadata.height; y += cellSize )
      {
         let points = [];
         for ( let x = 0; x - cellSize <= metadata.width; x += cellSize )
         {
            let posLinearI = new Point( x, y );
            let posG = ref_I_G_linear.apply( posLinearI );
            points.push( metadata.ref_G_I.apply( posG ) );
         }
         g.drawPolyline( points );
      }
      for ( let x = 0; x - cellSize <= metadata.width; x += cellSize )
      {
         let points = [];
         for ( let y = 0; y - cellSize <= metadata.height; y += cellSize )
         {
            let posLinearI = new Point( x, y );
            let posG = ref_I_G_linear.apply( posLinearI );
            points.push( metadata.ref_G_I.apply( posG ) );
         }
         g.drawPolyline( points );
      }
      g.end();

      let window = new ImageWindow( metadata.width, metadata.height,
                                    3/*channels*/, 8/*bits*/, false/*float*/, true/*color*/,
                                    targetWindow.mainView.id + "_distortions" );
      window.mainView.beginProcess( UndoFlag_NoSwapFile );
      window.mainView.image.blend( bmp );
      window.keywords = targetWindow.keywords;
      window.mainView.endProcess();
      window.show();
   };

   this.GenerateDistortionModel = function( metadata, path )
   {
      console.writeln( "<end><cbr>Generating distortion model: ", path );

      let file = new File();
      try
      {
         file.create( path );

         if ( metadata.ref_I_G.order === undefined || metadata.ref_I_G.order <= 2 )
            file.outTextLn( "ThinPlate,2" );
         else
            file.outTextLn( "2DSurfaceSpline," + metadata.ref_I_G.order.toString() );

         let ref_I_G_linear = metadata.ref_I_G_linear;
         if ( metadata.controlPoints )
         {
            let centerI = new Point( metadata.width / 2, metadata.height / 2 );
            let centerG = metadata.ref_I_G.apply( centerI );
            ref_I_G_linear = MultipleLinearRegressionHelmert( metadata.controlPoints.pI, metadata.controlPoints.pG, centerI, centerG );
         }

         // Total points: 46*46 = 2116
         for ( let y = 0; y <= 45; ++y )
            for ( let x = 0; x <= 45; ++x )
            {
               let posLinearI = new Point( metadata.width/45 * x, metadata.height/45 * y );
               let posG = ref_I_G_linear.apply( posLinearI );
               let posDistortI = metadata.ref_G_I.apply( posG );
               let dx = posDistortI.x - posLinearI.x;
               let dy = posDistortI.y - posLinearI.y;
               file.outTextLn( format( "%.6f,%.6f,%.6f,%.6f", posLinearI.x, posLinearI.y, dx, dy ) );
            }
      }
      finally
      {
         file.close();
      }
   };

   // This warning is now silenced.
   this.showedWarningOnTruncatedInputSet = true; //false;

   this.DetectStars = function( window, metadata )
   {
      /*
       * Load reference stars.
       */
      if ( !this.catalog )
         if ( this.solverCfg.catalogMode == CatalogMode.prototype.LocalText )
         {
            this.catalog = new CustomCatalog( this.solverCfg.databasePath );
         }
         else
         {
            this.catalog = __catalogRegister__.GetCatalog( this.catalogName );
            this.catalog.magMax = this.limitMagnitude;
            this.catalog.restrictToHQStars = this.solverCfg.restrictToHQStars;
         }
      this.catalog.reportObjectsInside = false;
      this.catalog.Load( metadata, this.solverCfg.vizierServer );
      let catalogObjects = this.catalog.objects;
      if ( catalogObjects == null )
         throw "Catalog error: " + this.catalogName;
      if ( catalogObjects.length < WCS_MIN_CATALOG_STARS )
         throw "Insufficient stars found in catalog: " + this.catalogName;
      if ( catalogObjects.length > WCS_MAX_CATALOG_STARS )
         if ( !this.showedWarningOnTruncatedInputSet )
         {
            console.warningln( "<end><cbr>** Warning: Exceeded the maximum number of stars allowed. " +
               "Truncating the input set to the ", WCS_MAX_CATALOG_STARS, " brightest stars." );
            this.showedWarningOnTruncatedInputSet = true;
         }

      /*
       * Sort reference stars by magnitude in ascending order (brighter stars
       * first). Possible objects with undefined magnitudes are packed at the
       * tail of the array.
       */
      catalogObjects.sort( (a, b) => a.magnitude ? (b.magnitude ? a.magnitude - b.magnitude : -1) : (b.magnitude ? +1 : 0) );

      /*
       * Calculate image coordinates of catalog stars with the current
       * transformation.
       */
      let result = {
         projection: ProjectionFactory( this.solverCfg, metadata.ra, metadata.dec ),
         starCoords: [],
         coordsG: [],
         magnitudes: [],
         actualCoords: null
      };
      let predictedCoords = [];
      {
         let posRD = [], magnitudes = [];
         for ( let i = 0, n = Math.min( WCS_MAX_CATALOG_STARS, catalogObjects.length ); i < n; ++i )
            if ( catalogObjects[i] )
            {
               posRD.push( catalogObjects[i].posRD );
               magnitudes.push( catalogObjects[i].magnitude );
            }
         let posI = metadata.Convert_RD_I_Points( posRD, true/*unscaled*/ );

         for ( let i = 0; i < posI.length; ++i )
         {
            let pI = posI[i];
            if ( pI &&
                 pI.x >= 0 &&
                 pI.y >= 0 &&
                 pI.x <= metadata.width &&
                 pI.y <= metadata.height )
            {
               let pG = result.projection.Direct( posRD[i] );
               if ( pG )
               {
                  result.coordsG.push( pG );
                  result.starCoords.push( posRD[i] );
                  result.magnitudes.push( magnitudes[i] );
                  predictedCoords.push( pI );
               }
            }
         }
      }

      if ( predictedCoords.length < 4 )
         throw "Unable to define a valid set of reference star positions.";

      /*
       * Find the stars in the image using predictedCoords as starting point.
       */
      let matches = this.MatchStars( window, predictedCoords );

      result.actualCoords = matches.matchedPoints;

      /*
       * Remove control points with identical coordinates.
       */
      {
         let A = [];
         for ( let i = 0; i < result.actualCoords.length; ++i )
            if ( result.actualCoords[i] )
               A.push( { i: i, x: result.actualCoords[i].x, y: result.actualCoords[i].y } );
         A.sort( (a,b) => (a.x != b.x) ? a.x - b.x : a.y - b.y );
         for ( let i = 1; i < A.length; ++i )
            if ( A[i].x == A[i-1].x )
               if ( A[i].y == A[i-1].y )
               {
                  result.actualCoords[A[i].i] = null;
                  result.coordsG[A[i].i] = null;
               }
         A = [];
         for ( let i = 0; i < result.coordsG.length; ++i )
            if ( result.coordsG[i] )
               A.push( { i: i, x: result.coordsG[i].x, y: result.coordsG[i].y } );
         A.sort( (a,b) => (a.x != b.x) ? a.x - b.x : a.y - b.y );
         for ( let i = 1; i < A.length; ++i )
            if ( A[i].x == A[i-1].x )
               if ( A[i].y == A[i-1].y )
               {
                  result.actualCoords[A[i].i] = null;
                  result.coordsG[A[i].i] = null;
               }
      }

      if ( this.solverCfg.showStarMatches )
         this.DrawStars( window, metadata, result.actualCoords, window.mainView.id + "_matched" );

      /*
       * Gather information on matching errors.
       */
      result.errors = new Array( predictedCoords.length );
      result.numValid = 0;
      let meanError, sigmaError, peakError = 0, sum2 = 0;
      {
         let E = [];
         for ( let i = 0; i < predictedCoords.length; ++i )
            if ( result.actualCoords[i] )
            {
               let ex = predictedCoords[i].x - result.actualCoords[i].x;
               let ey = predictedCoords[i].y - result.actualCoords[i].y;
               let e = Math.sqrt( ex*ex + ey*ey );
               result.errors[i] = e;
               E.push( e );
               if ( e > peakError )
                  peakError = e;
               result.numValid++;
               sum2 += e*e;
            }

         meanError = Math.median( E );
         sigmaError = Math.sqrt( Math.biweightMidvariance( E, meanError ) );
      }
      result.rms = (result.numValid > 0) ? Math.sqrt( sum2 / result.numValid ) : 0;
      result.score = Math.roundTo( result.numValid/(1 + result.rms), 3 );

      if ( this.solverCfg.distortionCorrection )
         console.writeln( format( "Surface sparsity : median = %.2f, sigma = %.2f, threshold = %.2f",
                                 matches.meanSparsity, matches.sigmaSparsity, matches.rejectionThreshold ) );
      console.writeln( format(    "Matching errors  : median = %.2f px, sigma = %.2f px, peak = %.2f px",
                                 meanError, sigmaError, peakError ) );
      console.writeln( format(    "Matched stars    : %d (%.2f%%)",
                                 result.numValid, 100.0*result.numValid/predictedCoords.length ) );
      console.flush();

      return result;
   };

   this.DoIterationLinear = function( metadata, stars )
   {
      console.flush();
      processEvents();

      /*
       * Find linear transformations.
       */
      let newMetadata = metadata.Clone();
      newMetadata.projection = stars.projection;
      newMetadata.ref_I_G = Math.homography( stars.actualCoords, stars.coordsG );
      newMetadata.ref_I_G_linear = newMetadata.ref_I_G;
      newMetadata.ref_G_I = newMetadata.ref_I_G.inverse();
      newMetadata.controlPoints = null;

      /*
       * Find the celestial coordinates (RD) of the center of the original
       * image. First transform from I to G and then unproject from native
       * projection coordinates (G) to celestial (RD).
       */
      let centerI = new Point( metadata.width / 2, metadata.height / 2 );
      let centerG = newMetadata.ref_I_G.apply( centerI );
      let centerRD = newMetadata.projection.Inverse( centerG );
      while ( centerRD.x < 0 )
         centerRD.x += 360;
      while ( centerRD.x >= 360 )
         centerRD.x -= 360;
      newMetadata.ra = (Math.abs( metadata.ra - centerRD.x ) < 1) ? (metadata.ra + centerRD.x * 2) / 3 : centerRD.x;
      newMetadata.dec = (Math.abs( metadata.dec - centerRD.y ) < 1) ? (metadata.dec + centerRD.y * 2) / 3 : centerRD.y;
      let ref = newMetadata.ref_I_G_linear;
      let resx = Math.sqrt( ref.at( 0, 0 ) * ref.at( 0, 0 ) + ref.at( 0, 1 ) * ref.at( 0, 1 ) );
      let resy = Math.sqrt( ref.at( 1, 0 ) * ref.at( 1, 0 ) + ref.at( 1, 1 ) * ref.at( 1, 1 ) );
      newMetadata.resolution = ( resx + resy ) / 2;
      newMetadata.focal = newMetadata.FocalFromResolution( newMetadata.resolution );
      newMetadata.useFocal = false;

      return newMetadata;
   };

   this.DoIterationSpline = function( metadata, stars )
   {
      console.flush();
      processEvents();

      /*
       * Build surface splines.
       */
      let newMetadata = metadata.Clone();
      newMetadata.projection = stars.projection;
      newMetadata.ref_I_G_linear = Math.homography( stars.actualCoords, stars.coordsG );
      newMetadata.ref_I_G = new ReferSpline( stars.actualCoords, stars.coordsG,
                                             this.solverCfg.rbfType,
                                             this.solverCfg.maxSplinePoints,
                                             this.solverCfg.splineOrder,
                                             this.solverCfg.splineSmoothing,
                                             this.solverCfg.enableSimplifier,
                                             this.solverCfg.simplifierRejectFraction );
      newMetadata.ref_G_I = newMetadata.ref_I_G.inverse;

      processEvents();

      newMetadata.controlPoints = {
         pI: stars.actualCoords,
         pG: stars.coordsG,
         weights: null
      };

      /*
       * Find the celestial coordinates (RD) of the center of the original
       * image. First transform from I to G and then unproject from native
       * projection coordinates (G) to celestial (RD).
       */
      let centerI = new Point( metadata.width / 2, metadata.height / 2 );
      let centerG = newMetadata.ref_I_G.apply( centerI );
      let centerRD = newMetadata.projection.Inverse( centerG );
      while ( centerRD.x < 0 )
         centerRD.x += 360;
      while ( centerRD.x >= 360 )
         centerRD.x -= 360;
      newMetadata.ra = (Math.abs( metadata.ra - centerRD.x ) < 1) ? (metadata.ra + centerRD.x * 2) / 3 : centerRD.x;
      newMetadata.dec = (Math.abs( metadata.dec - centerRD.y ) < 1) ? (metadata.dec + centerRD.y * 2) / 3 : centerRD.y;
      let ref = newMetadata.ref_I_G_linear;
      let resx = Math.sqrt( ref.at( 0, 0 ) * ref.at( 0, 0 ) + ref.at( 0, 1 ) * ref.at( 0, 1 ) );
      let resy = Math.sqrt( ref.at( 1, 0 ) * ref.at( 1, 0 ) + ref.at( 1, 1 ) * ref.at( 1, 1 ) );
      newMetadata.resolution = ( resx + resy ) / 2;
      newMetadata.focal = newMetadata.FocalFromResolution( newMetadata.resolution );
      newMetadata.useFocal = false;

      return newMetadata;
   };

   this.GenerateWorkingImage = function( targetWindow )
   {
      // Convert the image to grayscale.
      // The chrominance is not necessary for the astrometry.
      let grayscaleImage = new Image;
      grayscaleImage.assign( targetWindow.mainView.image );
      grayscaleImage.colorSpace = ColorSpace_HSI;
      grayscaleImage.selectedChannel = 2; // intensity component

      let workingWindow = new ImageWindow( grayscaleImage.width, grayscaleImage.height,
                                    1/*channels*/, 32/*bits*/, true/*float*/, false/*color*/,
                                    targetWindow.mainView.id + "_working" );
      workingWindow.mainView.beginProcess( UndoFlag_NoSwapFile );
      workingWindow.mainView.image.apply( grayscaleImage );
      workingWindow.mainView.endProcess();

      // Deallocate now, don't wait for garbage collection.
      grayscaleImage.free();

      return workingWindow;
   };

   this.MetadataDelta = function( metadata1, metadata2, pI )
   {
      /*
       * Calculate the difference between the last two iterations using the
       * displacement between the center and the given point pI.
       */
      let pRD2 = metadata2.Convert_I_RD( pI );
      let pRD1 = metadata1.ref_I_G ? metadata1.Convert_I_RD( pI ) : pRD2;
      let delta1 = 0;
      if ( pRD1 )
         delta1 = Math.sqrt( Math.pow( (pRD1.x - pRD2.x) * Math.cos( Math.rad( pRD2.y ) ), 2 ) +
                  Math.pow( pRD1.y - pRD2.y, 2 ) ) * 3600;
      let delta2 = Math.sqrt( Math.pow( (metadata2.ra - metadata1.ra) * Math.cos( Math.rad( metadata2.dec ) ), 2 ) +
                  Math.pow( metadata2.dec - metadata1.dec, 2 ) ) * 3600;
      return Math.max( delta1, delta2 );
   };

   this.OptimizeSolution = function( workingWindow, currentMetadata, stars )
   {
      let finished = false;
      let iteration = 1;
      let numItersWithoutImprovement = 0;
      let maxItersWithoutImprovement = 4;
      let bestMetadata = currentMetadata;
      let bestScore = stars.score;
      let bestRMS = stars.rms;
      let bestStarCount = stars.numValid;
      let converged = false;

      do
      {
         console.abortEnabled = true;
         let result;
         try
         {
            if ( this.solverCfg.distortionCorrection )
               result = this.DoIterationSpline( currentMetadata, stars );
            else
               result = this.DoIterationLinear( currentMetadata, stars );

            if ( result == null )
               throw "";
         }
         catch ( ex )
         {
            let haveException = !console.isAborted && (ex.length === undefined || ex.length > 0);
            if ( haveException )
               console.criticalln( "<end><cbr><br>*** Error: " + ex.toString() );
            console.criticalln( "<end><cbr>" +
               (haveException ? "" : "<br>*** Error: ") +
               "The image could not be fully solved. We have tagged it with the latest known valid solution." );
            console.abortEnabled = false;
            break;
         }

         stars = this.DetectStars( workingWindow, result );

         /*
          * Calculate the difference between the current and previous
          * iterations using the displacements between the center and eight
          * points located on the image borders. Report the maximum difference.
          */
         let delta = Math.max( this.MetadataDelta( currentMetadata, result, new Point( 0, 0 ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( result.width, 0 ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( 0, result.height ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( result.width, result.height ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( result.width/2, 0 ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( result.width/2, result.height ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( 0, result.height/2 ) ),
                               this.MetadataDelta( currentMetadata, result, new Point( result.width, result.height/2 ) ) );
         let deltaPx = delta/(result.resolution * 3600);

         console.writeln( "<end><cbr><br>*****" );
         console.writeln(    format( "Iteration %d, delta = %.3f as (%.2f px)", iteration, delta, deltaPx ) );
         console.writeln(            "Image center ... RA: ", DMSangle.FromAngle( result.ra / 15 ).ToString( true ),
                                                   "  Dec: ", DMSangle.FromAngle( result.dec ).ToString() );
         console.writeln(    format( "Resolution ..... %.2f as/px", result.resolution * 3600 ) );
         console.writeln(    format( "RMS error ...... %.3f px (%d stars)", stars.rms, stars.numValid ) );

         converged = deltaPx < 0.005 && Math.abs( stars.rms - bestRMS ) < 0.01;
         if ( converged || stars.numValid > bestStarCount && (stars.rms <= bestRMS || stars.rms - bestRMS < 0.01) )
            stars.score = Math.max( stars.score, bestScore + 1 );

         if ( stars.score > bestScore )
            console.writeln( format( "Score .......... \x1b[38;2;128;255;128m%.3f\x1b[0m", stars.score ) );
         else
            console.writeln( format( "Score .......... %.3f", stars.score ) );
         console.writeln( "*****" );

         /*
          * Prevent degenerate cases where we cannot match any stars. This
          * happens, among other causes, when projection systems are used
          * beyond their capabilities.
          */
         if ( stars.numValid < 4 )
         {
            console.criticalln( "*** Error: Unable to find a valid set of star pair matches." );
            break;
         }

         currentMetadata = result;

         // Store the best model so far
         if ( stars.score > bestScore )
         {
            numItersWithoutImprovement = 0;
            bestMetadata = result;
            bestScore = stars.score;
            bestRMS = stars.rms;
            bestStarCount = stars.numValid;
         }
         else
         {
            if ( iteration == 1 )
               bestMetadata = result;
            numItersWithoutImprovement++;
         }

         // Finish condition
         finished = true;
         if ( converged || numItersWithoutImprovement > maxItersWithoutImprovement )
         {
            if ( converged )
               console.noteln( format( "<end><cbr><br>* Convergence reached after %d iterations.", iteration ) );
            else
               console.noteln( format( "<end><cbr><br>* Process stalled after %d iterations.", iteration ) );
         }
         else if ( iteration > this.solverCfg.maxIterations )
            console.warningln( "<end><cbr><br>** Warning: Reached maximum number of iterations without convergence." );
         else
            finished = false;

         ++iteration;

         console.abortEnabled = true;
         processEvents();
         if ( console.abortRequested )
         {
            finished = true;
            console.criticalln( "*** User requested abort ***" );
         }
         gc( true );
      }
      while ( !finished );

      console.writeln();

      return bestMetadata;
   };

   this.SolveImage = function( targetWindow )
   {
      this.error = false;

      let abortableBackup = jsAbortable;
      jsAbortable = true;
      let auxWindow = null;

      try
      {
         console.show();
         console.abortEnabled = true;

         let workingWindow = targetWindow;
         if ( targetWindow.mainView.image.isColor )
            auxWindow = workingWindow = this.GenerateWorkingImage( targetWindow );

         /*
          * Build a bucket region quadtree structure with all detected stars in
          * the image for fast star matching.
          */
         try
         {
            /*
             * Step 1 - Star detection
             */
            let D = new StarDetector;
            D.structureLayers = this.solverCfg.structureLayers;
            D.hotPixelFilterRadius = this.solverCfg.hotPixelFilterRadius;
            D.noiseReductionFilterRadius = this.solverCfg.noiseReductionFilterRadius;
            D.sensitivity = this.solverCfg.sensitivity;
            D.peakResponse = this.solverCfg.peakResponse;
            D.allowClusteredSources = false;
            D.maxDistortion = this.solverCfg.maxStarDistortion;
            D.brightThreshold = this.solverCfg.brightThreshold;
            D.minStructureSize = this.solverCfg.minStructureSize;
            let lastProgressPc = 0;
            D.progressCallback =
               ( count, total ) =>
               {
                  if ( count == 0 )
                  {
                     console.write( "<end><cbr>Detecting stars:   0%" );
                     lastProgressPc = 0;
                     processEvents();
                  }
                  else
                  {
                     let pc = Math.round( 100*count/total );
                     if ( pc > lastProgressPc )
                     {
                        console.write( format( "<end>\b\b\b\b%3d%%", pc ) );
                        lastProgressPc = pc;
                        processEvents();
                     }
                  }
                  return true;
               };

            let S = D.stars( workingWindow.mainView.image );
            this.numberOfDetectedStars = S.length;
            if ( this.numberOfDetectedStars < 6 )
               throw "Insufficient stars detected: found " + this.numberOfDetectedStars.toString() + ", at least 6 are required.";

            console.writeln( format( "<end><cbr>%d stars found ", this.numberOfDetectedStars ) );
            console.flush();

            /*
             * Step 2 - PSF fitting
             */
            let stars = [];
            let minStructSize = Number.POSITIVE_INFINITY;
            for ( let i = 0; i < S.length; ++i )
            {
               let p = S[i].pos;
               let r = S[i].rect;
               stars.push( [ 0, 0, DynamicPSF.prototype.Star_DetectedOk,
                             r.x0, r.y0, r.x1, r.y1,
                             p.x, p.y ] );
               let m = Math.max( r.x1 - r.x0, r.y1 - r.y0 );
               if ( m < minStructSize )
                  minStructSize = m;
            }

            let P = new DynamicPSF;
            P.views = [ [ workingWindow.mainView.id ] ];
            P.stars = stars;
            P.astrometry = false;
            P.autoAperture = true;
            P.searchRadius = minStructSize;
            P.circularPSF = false;
            P.autoPSF = this.solverCfg.autoPSF;
            P.gaussianPSF = true;
            P.moffatPSF = P.moffat10PSF = P.moffat8PSF =
               P.moffat6PSF = P.moffat4PSF = P.moffat25PSF =
               P.moffat15PSF = P.lorentzianPSF = this.solverCfg.autoPSF;
            P.variableShapePSF = false;
            if ( !P.executeGlobal() )
               throw "Unable to execute DynamicPSF process.";

            console.flush();

            stars = [];
            for ( let psf = P.psf, i = 0; i < psf.length; ++i )
            {
               let p = psf[i];
               if ( p[3] == DynamicPSF.prototype.PSF_FittedOk )
               {
                  let x = p[6];
                  let y = p[7];
                  let rx = p[8]/2;
                  let ry = p[9]/2;
                  stars.push( {
                     x: x, y: y,
                     rect: {
                        x0: x - rx, y0: y - ry,
                        x1: x + rx, y1: y + ry
                     }
                  } );
               }
            }

            /*
             * Step 3 - Remove conflicting sources
             */
            this.starTree = new BRQuadTree( stars.slice(), 256/*bucketSize*/ );
            stars = [];
            for ( let i = 0; i < this.starTree.objects.length; ++i )
            {
               let o = this.starTree.objects[i];
               let s = this.starTree.search( { x0: o.x - 1, y0: o.y - 1,
                                               x1: o.x + 1, y1: o.y + 1 } );
               if ( s.length == 1 )
                  stars.push( o );
            }
            if ( stars.length < 6 )
               throw "Insufficient number of objects: found " + stars.length.toString() + ", at least 6 are required.";

            console.write( format( "<end><cbr>* Removed %d conflicting sources (%.2f %%)",
                                   this.starTree.objects.length - stars.length, 100*(this.starTree.objects.length - stars.length)/stars.length ) );

            /*
             * Step 4 - Quadtree generation
             */
            this.starTree.build( stars.slice(), 256/*bucketSize*/ );
            console.write( format( "<end><cbr>* Search quadtree generated with %d objects, %d node(s), height = %d",
                                   this.starTree.objects.length, this.starTree.numberOfNodes(), this.starTree.height() ) );

            /*
             * Step 5 - Calculate search and matching tolerances
             */
            this.psfMinimumDistance = Math.min( stars[0].rect.x1 - stars[0].rect.x0,
                                                stars[0].rect.y1 - stars[0].rect.y0 );
            for ( let i = 1; i < stars.length; ++i )
            {
               let s = stars[i];
               let d = Math.min( stars[i].rect.x1 - stars[i].rect.x0,
                                 stars[i].rect.y1 - stars[i].rect.y0 );
               if ( d < this.psfMinimumDistance )
                  this.psfMinimumDistance = d;
            }
            this.psfMinimumDistance = Math.max( 2, Math.trunc( 0.75*(this.psfMinimumDistance - 2) ) ); // StarDetector inflates detection regions
            this.psfSearchRadius = 1.0 * this.psfMinimumDistance;
            console.writeln( format( "<end><cbr>* Star matching tolerance: %d px", this.psfMinimumDistance ) );
            console.flush();
         }
         catch ( ex )
         {
            this.starTree = null;
            gc();
            throw ex;
         }

         /*
          * Generate star maps right after star detection.
          */
         if ( this.solverCfg.showStars )
            this.DrawStars( targetWindow, this.metadata, this.starTree.objects );

         /*
          * Find limit magnitude.
          */
         if ( this.solverCfg.autoMagnitude || this.solverCfg.catalogMode == CatalogMode.prototype.Automatic )
         {
            let fov = this.metadata.resolution * Math.max( this.metadata.width, this.metadata.height );
            // Empiric formula for 1000 stars at 20 deg of galactic latitude
            let m = 14.5 * Math.pow( fov, -0.179 );
            m = Math.round( 100 * Math.min( 20, Math.max( 7, m ) ) ) / 100;

            /*
             * Identify a local XPSD server and use it if available to find an
             * optimal magnitude limit adaptively.
             */
            let xpsd = ((typeof Gaia) != 'undefined') ? (new Gaia) : null;
            if ( xpsd )
            {
               xpsd.command = "get-info";
               xpsd.dataRelease = Gaia.prototype.DataRelease_BestAvailable;
               xpsd.executeGlobal();
               if ( xpsd.isValid )
               {
                  if ( this.solverCfg.autoMagnitude )
                  {
                     const radiusPx = Math.SQRT2 * Math.sqrt( this.metadata.width * this.metadata.height ) / 2;
                     const targetStarCount = this.numberOfDetectedStars * 1.25;

                     console.writeln( format( "<end><cbr><br>Searching for optimal magnitude limit. Target: %u stars", targetStarCount ) );

                     xpsd.command = "search";
                     xpsd.centerRA = this.metadata.ra;
                     xpsd.centerDec = this.metadata.dec;
                     xpsd.radius = this.metadata.resolution * radiusPx;
                     xpsd.magnitudeLow = -1.5;
                     xpsd.sourceLimit = 0; // do not retrieve objects, just count them.
                     xpsd.exclusionFlags = GaiaFlag_NoPM;
                     xpsd.inclusionFlags = this.solverCfg.restrictToHQStars ? GaiaFlag_GoodAstrometry : 0;
                     xpsd.verbosity = 0; // work quietly
                     xpsd.generateTextOutput = false;

                     const MAX_AUTOMAG_ITER = 100; // prevent a hypothetical case where the loop might stall
                     for ( let m0 = 7, m1 = xpsd.databaseMagnitudeHigh, i = 0; i < MAX_AUTOMAG_ITER; ++i )
                     {
                        xpsd.magnitudeHigh = m;
                        xpsd.executeGlobal();
                        console.writeln( format( "<end><cbr>m = %.2f, %u stars", m, xpsd.excessCount ) );
                        if ( xpsd.excessCount < targetStarCount )
                        {
                           if ( m1 - m < 0.05 )
                              break;
                           m0 = m;
                           m += (m1 - m)/2;
                        }
                        else if ( xpsd.excessCount > 1.05*targetStarCount )
                        {
                           if ( m - m0 < 0.05 )
                              break;
                           m1 = m;
                           m -= (m - m0)/2;
                        }
                        else
                           break;
                     }
                  }
               }
               else
               {
                  /*
                   * We have a local XPSD server, but either it is not well
                   * configured, or there are no database files available.
                   */
                  xpsd = null;
               }
            }

            if ( this.solverCfg.autoMagnitude )
            {
               this.limitMagnitude = m;
               console.noteln( "<end><cbr><br>* Using an automatically calculated limit magnitude of " + format( "%.2f", m ) + "." );
            }
            else
               this.limitMagnitude = this.solverCfg.magnitude;

            if ( this.solverCfg.catalogMode == CatalogMode.prototype.Automatic )
            {
               /*
                * - Use a local Gaia XPSD server when available and m > 5.
                * - Otherwise:
                * - For m <= 7.0, use the online Bright Stars catalog.
                * - For FOV <= 3 deg, use the online Gaia DR2 catalog.
                * - For FOV > 3 deg, use the online TYCHO-2 catalog.
                */
               if ( xpsd && this.limitMagnitude > 5 )
               {
                  switch ( xpsd.outputDataRelease )
                  {
                  default:
                  case Gaia.prototype.DataRelease_3:
                     this.catalogName = "GaiaDR3_XPSD";
                     break;
                  case Gaia.prototype.DataRelease_E3:
                     this.catalogName = "GaiaEDR3_XPSD";
                     break;
                  case Gaia.prototype.DataRelease_2:
                     this.catalogName = "GaiaDR2_XPSD";
                     break;
                  }
               }
               else
               {
                  if ( this.limitMagnitude <= 7 )
                     this.catalogName = "Bright Stars";
                  else if ( fov > 3 )
                     this.catalogName = "TYCHO-2";
                  else
                     this.catalogName = "GaiaDR2";
               }

               console.noteln( "<end><cbr>* Using the automatically selected " + this.catalogName + " catalog." );
            }
            else
               this.catalogName = this.solverCfg.catalog;
         }
         else
         {
            this.limitMagnitude = this.solverCfg.magnitude;
            this.catalogName = this.solverCfg.catalog;
         }

         console.writeln( "Seed parameters for plate solving:" );
         console.writeln( "   Center coordinates: RA = ",
            DMSangle.FromAngle( this.metadata.ra / 15 ).ToString( true ), ", Dec = ",
            DMSangle.FromAngle( this.metadata.dec ).ToString() );
         console.writeln( format( "   Resolution: %.3f as/px", this.metadata.resolution * 3600 ) );
         console.writeln();

         let stars = null;

         /*
          * Initial Alignment.
          */
         try
         {
            let result = this.DoIterationSA( targetWindow, this.metadata );
            if ( !result )
               throw "";
            this.metadata = result;

            stars = this.DetectStars( workingWindow, this.metadata );

            console.writeln( "<end><cbr><br>*****" );
            console.writeln(         "Initial alignment" );
            console.writeln(         "Image center ... RA: ", DMSangle.FromAngle( this.metadata.ra / 15 ).ToString( true ),
                                                   "  Dec: ", DMSangle.FromAngle( this.metadata.dec ).ToString() );
            console.writeln( format( "Resolution ..... %.2f as/px", this.metadata.resolution * 3600 ) );
            console.writeln( format( "RMS error ...... %.3f px (%d stars)", stars.rms, stars.numValid ) );
            console.writeln( format( "Score .......... %.3f", stars.score ) );
            console.writeln( "*****" );
         }
         catch ( ex )
         {
            if ( !console.isAborted )
               if ( ex.length === undefined || ex.length > 0 )
                  console.criticalln( "<end><cbr><br>*** Error: " + ex.toString() );
            this.error = true;
            return false;
         }

         /*
          * Optimize the solution.
          */
         this.metadata = this.OptimizeSolution( workingWindow, this.metadata, stars );

         /*
          * Update metadata and regenerate the astrometric solution.
          */
         targetWindow.mainView.beginProcess( UndoFlag_Keywords | UndoFlag_AstrometricSolution );
         this.metadata.SaveKeywords( targetWindow, false/*beginProcess*/ );
         this.metadata.SaveProperties( targetWindow, "ImageSolver " + SOLVERVERSION, this.catalog.name );
         targetWindow.regenerateAstrometricSolution();
         targetWindow.mainView.endProcess();

         /*
          * Generate a distortion model if requested.
          */
         if ( this.solverCfg.distortionCorrection && this.solverCfg.generateDistortModel )
         {
            let modelPath = null;
            let filePath = targetWindow.filePath;
            if ( filePath.length > 0 )
            {
               let modelDir = File.extractDrive( filePath ) +
                  File.extractDirectory( filePath );
               let info = new FileInfo( modelDir );
               if ( info.isWritable )
               {
                  if ( !modelDir.endsWith( '/' ) )
                     modelDir += '/';
                  modelPath = modelDir +
                     File.extractName( filePath ) +
                     "_model.csv";
               }
            }

            if ( modelPath == null )
            {
               let ofd = new SaveFileDialog;
               ofd.caption = "Save Distortion Model File";
               ofd.filters = [
                  [ "Distortion models", "*.csv" ]
               ];
               if ( filePath.length > 0 )
                  ofd.initialPath = File.changeExtension( filePath, ".csv" );
               if ( ofd.execute() )
                  modelPath = ofd.fileName;
            }

            if ( modelPath != null )
               this.GenerateDistortionModel( this.metadata, modelPath );
         }

         /*
          * Generate the requested control images.
          */
         if ( this.solverCfg.distortionCorrection )
         {
            if ( this.solverCfg.showDistortion )
               this.DrawDistortions( targetWindow, this.metadata );

            if ( this.solverCfg.enableSimplifier )
               if ( this.solverCfg.showSimplifiedSurfaces )
                  this.DrawSimplifiedSurfaces( targetWindow, this.metadata );
         }

         if ( this.solverCfg.generateErrorImg )
         {
            stars = this.DetectStars( workingWindow, this.metadata );
            this.DrawErrors( targetWindow, this.metadata, stars );
         }

         return true;
      }
      catch ( ex )
      {
         this.error = true;
         throw ex;
      }
      finally
      {
         jsAbortable = abortableBackup;
         if ( auxWindow )
            auxWindow.forceClose();
      }
   };

   this.SaveImage = function( window )
   {
      let newPath = File.extractDrive( window.filePath ) +
                    File.extractDirectory( window.filePath ) + "/" +
                    File.extractName( window.filePath ) + this.solverCfg.outSuffix +
                    ".xisf";
      window.saveAs( newPath,
         false /*queryOptions*/ ,
         false /*allowMessages*/ ,
         true  /*strict*/ ,
         false /*verifyOverwrite*/ );
   };
}

// ----------------------------------------------------------------------------
// Entry point
// ----------------------------------------------------------------------------

#ifndef USE_SOLVER_LIBRARY

function main()
{
   function printResult( window )
   {
      console.writeln( "<end><cbr><br>" + "=".repeat( 98 ) );
      console.writeln( window.astrometricSolutionSummary().trim() );
      console.writeln( "=".repeat( 98 ) );
   }

   jsScriptInformation = "ImageSolver " + SOLVERVERSION;

   if ( Parameters.getBoolean( "resetSettingsAndExit" ) )
   {
      Settings.remove( SETTINGS_MODULE );
      return;
   }

   if ( Parameters.getBoolean( "resetSettings" ) )
      Settings.remove( SETTINGS_MODULE );

   let solver = new ImageSolver;

   if ( Parameters.isViewTarget )
   {
      let targetWindow = Parameters.targetView.window;

      solver.Init( Parameters.targetView.window );

      if ( solver.SolveImage( targetWindow ) )
      {
         solver.metadata.SaveSettings();
         printResult( targetWindow );
         ++__PJSR_AdpImageSolver_SuccessCount;
      }
   }
   else
   {
      let targetWindow = ImageWindow.activeWindow;

      if ( Parameters.getBoolean( "non_interactive" ) )
         solver.Init( targetWindow, false /*prioritizeSettings*/ );
      else
      {
         let dialog;
         for ( ;; )
         {
            solver.Init( targetWindow, false /*prioritizeSettings*/ );
            dialog = new ImageSolverDialog( solver.solverCfg, solver.metadata, true /*showTargetImage*/ );
            if ( dialog.execute() )
               break;
            if ( !dialog.resetRequest )
               return;
            solver = new ImageSolver();
         }

         if ( solver.error )
            return;

         solver.solverCfg = dialog.solverCfg;
         solver.solverCfg.SaveSettings();

         solver.metadata = dialog.metadata;
         solver.metadata.SaveSettings();
      }

      if ( solver.solverCfg.useActive )
      {
         if ( solver.SolveImage( targetWindow ) )
         {
            solver.metadata.SaveSettings();
            printResult( targetWindow );
            ++__PJSR_AdpImageSolver_SuccessCount;
         }
      }
      else
      {
         if ( solver.solverCfg.files.length == 0 )
            throw "No image files have been selected.";
         let errorList = [];
         for ( let i = 0; i < solver.solverCfg.files.length; ++i )
         {
            let filePath = solver.solverCfg.files[i];
            let fileWindow = null;
            try
            {
               console.writeln( "<end><cbr><br>" + "*".repeat( 40 ) );
               console.writeln( "Processing file: <raw>" + filePath + "</raw>" );
               fileWindow = ImageWindow.open( filePath )[0];
               if ( !fileWindow )
               {
                  errorList.push(
                     {
                        id: File.extractNameAndExtension( filePath ),
                        message: "The file could not be opened"
                     } );
                  continue;
               }
               solver.Init( fileWindow, false /*prioritizeSettings*/ );
               solver.metadata.width = fileWindow.mainView.image.width;
               solver.metadata.height = fileWindow.mainView.image.height;
               if ( solver.SolveImage( fileWindow ) )
               {
                  solver.SaveImage( fileWindow );
                  console.writeln( "<end><cbr><br><raw>" + filePath + "</raw>" );
                  printResult( fileWindow );
                  ++__PJSR_AdpImageSolver_SuccessCount;
               }
               else
                  errorList.push(
                     {
                        id: File.extractNameAndExtension( filePath ),
                        message: "The image could not be solved"
                     } );
            }
            catch ( ex )
            {
               console.criticalln( "<end><cbr><br>" + '*'.repeat( 40 ) );
               console.criticalln( "Failed: <raw>" + filePath + "</raw>" +
                  ((!ex.length || ex.length > 0) ? ": " + ex.toString() : "") );
               console.criticalln( '*'.repeat( 40 ) );
               errorList.push(
                  {
                     id: File.extractNameAndExtension( filePath ),
                     message: ex.toString()
                  } );
            }

            if ( fileWindow )
               fileWindow.forceClose();

            gc( true/*exhaustive*/ );
         }

         console.writeln();
         if ( errorList.length > 0 )
         {
            console.warningln( "<end><cbr><br>** Warning: Process finished with errors:" );
            for ( let i = 0; i < errorList.length; ++i )
               console.criticalln( errorList[i].id +
                     ((errorList[i].message.length > 0) ? ": " + errorList[i].message : "") );
         }
         else
            console.noteln( "<end><cbr>* Process finished without errors." );
      }
   }
}

main();

#endif // !USE_SOLVER_LIBRARY

#undef USE_SOLVER_LIBRARY
