# TractVR

## Overview
TractVR is a 3D Slicer module developed for interactive tractography cleaning and manipulation in virtual reality. It is intended to support routine professional use through immersive visualization and direct interaction with fiber bundles. The module relies on functionalities provided by the [SlicerVirtualReality](https://github.com/KitwareMedical/SlicerVirtualReality) extension within 3D Slicer.

## Repository context
This repository contains the operational virtual reality module intended for routine professional use. It is separate from the repositories developed specifically for the experimental user study.

## Related repositories
- [TractDesktop](https://github.com/TinaNant28/TractDesktop) – operational desktop module for routine professional use
- [TractVRRandomisation](https://github.com/TinaNant28/TractVRRandomization) – study planning and session randomization module
- [TractVR_UserStudy](https://github.com/TinaNant28/TractVR_UserStudy) – VR module used in the experimental study
- [TractDesktop_UserStudy](https://github.com/TinaNant28/TractDesktop_UserStudy) – desktop module used in the experimental study

## Main features
- Virtual reality visualization of tractography data
- Interactive manipulation and cleaning of fiber bundles
- Immersive interaction through VR controllers
- Integration within a 3D Slicer workflow
- Use of VR functionalities provided by SlicerVirtualReality (SlicerVR)

## Intended users
This module is intended for trained professionals using tractography tools in a virtual reality environment.

## Dependencies
- 3D Slicer
- SlicerVirtualReality extension
- Python
- Other required Slicer libraries if applicable

## Installation
1. Install 3D Slicer.
2. Install the SlicerVirtualReality extension.
3. Clone or download this repository.
4. Add the module to your 3D Slicer environment.
5. Restart 3D Slicer if needed.

## Usage
1. Launch **3D Slicer**.
2. Open the **TractVR** module.
3. Load the required tractography data.
4. Click on **Create Cube** to create the ROI cube used for fiber editing.
5. Connect the VR headset and make sure that **Steam** and **SteamVR** are installed and running.
6. Start the VR environment by clicking on **Start VR**.
7. In VR environnement, move the cube using the controller to the region where fibers need to be edited or removed 
8. Click manually on **Update Fiber** to save the current edit or cleaning step.
9. Repeat the cube movement and update steps as needed until the fiber bundle is cleaned.
10. When the task is completed, click on **End** in the TractVR module.

## Notes
- TractVR uses SlicerVR to support VR visualization and controller-based interaction.  
For more details on how VR controllers are handled in 3D Slicer, please refer to the official [SlicerVirtualReality](https://github.com/KitwareMedical/SlicerVirtualReality) GitHub repository and developer documentation.
- The VR interaction in this project was implemented using the **OpenVR** backend
- This repository contains the operational version of the VR module. If you are looking for the version used specifically during the experimental study, please refer to the [TractVR_UserStudy](https://github.com/TinaNant28/TractVR_UserStudy) repository.

## Funding
This work was developed as part of a project funded by the Canada Research Chair in Neuroinformatics for Multimodal Data.  
Designated responsible investigator: Sylvain Bouix  
Reference number: CRC-2022-00183

## Acknowledgments
This module was adapted from templates and components from the 3D Slicer ecosystem. It also relies on functionalities provided by the SlicerVirtualReality extension.
