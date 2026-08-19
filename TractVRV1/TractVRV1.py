import logging
import os
from typing import Annotated, Optional

import vtk

import slicer
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import parameterNodeWrapper
import time
import csv
from datetime import datetime
from qt import QWidget, QObject, QEvent, QApplication, Qt, QTimer
import math


#
# TractVR
#

def _norm(v):
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])

def _angle_deg(a, b):
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    dot = (a[0]*b[0] + a[1]*b[1] + a[2]*b[2]) / (na*nb)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))

def _normalize(v):
    n = _norm(v)
    if n < 1e-8:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)

def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

# VR turntable rotation tuning (degrees/sec at full stick deflection)
VR_STICK_DEADZONE = 0.15
VR_YAW_SPEED_DEG_PER_SEC = 60.0
VR_PITCH_SPEED_DEG_PER_SEC = 60.0
VR_ROLL_SPEED_DEG_PER_SEC = 60.0


class TractVRV1(ScriptedLoadableModule):
    """
    Module for interacting with Markups Fiducials and ROI in VR within 3D Slicer.
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("TractVRV1")  # TODO: make this more human readable by adding spaces
        # TODO: set categories (folders where the module shows up in the module selector)
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Examples")]
        self.parent.dependencies = []  # TODO: add here list of module names that this module requires
        self.parent.contributors = ["Tina Nantenaina (Neuro-iX lab), Sylvain Bouix (Neuro-iX lab), Jarrett Rushmore (Boston University)"] 
        # TODO: update with short description of the module and a link to online module documentation
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("""
        This is an example of scripted loadable module bundled in an extension.
        See more information in <a href="https://github.com/organization/projectname#TractVR">module documentation</a>.
        """)
        # TODO: replace with organization, grant and thanks
        self.parent.acknowledgementText = _("""
        This file was adapted from a template originally developed by Jean-Christophe Fillion-Robin, Kitware Inc., Andras Lasso, PerkLab, and Steve Pieper, Isomics, Inc.
        This adapted work was developed as part of a project funded by the Canada Research Chair in Neuroinformatics for Multimodal Data.
        Designated responsible investigator: Sylvain Bouix
        Reference number: CRC-2022-00183
        """)


#
# TractVRParameterNode
#


@parameterNodeWrapper
class TractVRParameterNode: 
    inputVolume : slicer.vtkMRMLScalarVolumeNode 
    fiberBundle : slicer.vtkMRMLFiberBundleNode
   

#
# TractVRWidget
#


class TractVRV1Widget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/TractVRV1.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = TractVRV1Logic()
        self.logic.ui = self.ui

        # Connections

        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        # Buttons
        self.ui.startVR.clicked.connect(self.logic.onStartVR)
        self.ui.cubeButton.clicked.connect(self.logic.onCubeCreate)
        self.ui.sizeCube.valueChanged.connect(self.logic.onCubeSizeChanged)
        self.ui.saveFiber.clicked.connect(self.logic.onSaveFiber)
        self.ui.saveFiber.enabled = False
        self.ui.endTask.clicked.connect(self.logic.onEndTask)

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()

    def enter(self) -> None:
        """Called each time the user opens this module."""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            # self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """Called just before the scene is closed."""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        """Called just after the scene is closed."""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and observed."""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # Select default input nodes if nothing is selected yet to save a few clicks for the user
        # if not self._parameterNode.inputVolume:
        #     firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
        #     if firstVolumeNode:
        #         self._parameterNode.inputVolume = firstVolumeNode

    def setParameterNode(self, inputParameterNode: Optional[TractVRParameterNode]) -> None:
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """

        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            # self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
        self._parameterNode = inputParameterNode
        if self._parameterNode:
            # Note: in the .ui file, a Qt dynamic property called "SlicerParameterName" is set on each
            # ui element that needs connection.
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
    #         self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
    #         self._checkCanApply()

    # def _checkCanApply(self, caller=None, event=None) -> None:
    #     if self._parameterNode and self._parameterNode.inputVolume and self._parameterNode.thresholdedVolume:
    #         self.ui.applyButton.toolTip = _("Compute output volume")
    #         self.ui.applyButton.enabled = True
    #     else:
    #         self.ui.applyButton.toolTip = _("Select input and output volume nodes")
    #         self.ui.applyButton.enabled = False

    # def onApplyButton(self) -> None:
    #     """Run processing when user clicks "Apply" button."""
    #     with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
    #         # Compute output
    #         self.logic.process(self.ui.inputSelector.currentNode(), self.ui.outputSelector.currentNode(),
    #                            self.ui.imageThresholdSliderWidget.value, self.ui.invertOutputCheckBox.checked)

    #         # Compute inverted output (if needed)
    #         if self.ui.invertedOutputSelector.currentNode():
    #             # If additional output volume is selected then result with inverted threshold is written there
    #             self.logic.process(self.ui.inputSelector.currentNode(), self.ui.invertedOutputSelector.currentNode(),
    #                                self.ui.imageThresholdSliderWidget.value, not self.ui.invertOutputCheckBox.checked, showResult=False)


#
# TractVRLogic
#


class TractVRV1Logic(ScriptedLoadableModuleLogic, VTKObservationMixin):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ScriptedLoadableModuleLogic.__init__(self)
        VTKObservationMixin.__init__(self)
        self.vrLogic = None
        self.ui = None
        self.startTime = None
        self.endTime = None
        self.updateClickCount = 0
        self.cubeMoveCount = 0
        self.timeRunning = False

        # >>> NEW: variables de tracking d’interactions et caméra
        self.vrInteractor = None
        self._buttonObserversIds = []
        self._hmdObserverId = None
        self._headTransformNode = None

        # >>> NEW: right "B" button -> Update Fiber
        self._updateFiberObserverTag = None
        self._menuSuppressObserverTag = None
        self._hasPerControlEvents = False

        # >>> NEW: left stick turntable rotation + modifier (roll) button
        self._leftStickObserverTag = None
        self._rotationModifierObserverTag = None
        self._leftStickX = 0.0
        self._leftStickY = 0.0
        self._modifierHeld = False
        self._fiberPivotWorld = None
        self._lastRotationTickTime = None

        self.buttonPressCount = 0
        self.buttonReleaseCount = 0
        self.interactionCount = 0  # press→release
        self._pressedState = {}    # {(device,input): bool}

        self.hmdDistance_mm = 0.0
        self.hmdRotation_deg = 0.0
        self._lastHMDPos = None     # [x,y,z] en mm (RAS)
        self._lastHMDQuat = None    # (w,x,y,z) 

        self._sceneHMDObserverId = None 

        self._camEpsMm = 0.1      # seuil mm pour ignorer le micro-bruit
        self._camEpsDeg = 0.1     # seuil deg

        self.vrCamTransMm = 0.0   # distance cumulée du point de vue VR
        self.vrCamRotDeg  = 0.0   # rotation cumulée (changement d'axe de visée)
        self._vrCamLastPos = None
        self._vrCamLastDir = None
    

    def getParameterNode(self):
        return TractVRParameterNode(super().getParameterNode())

    # >>> NEW: utilitaires quaternion
    def _mat_to_quat(self, m: vtk.vtkMatrix4x4):
        # Convertit une 3x3 rotation en quaternion (w,x,y,z)
        r00, r01, r02 = m.GetElement(0,0), m.GetElement(0,1), m.GetElement(0,2)
        r10, r11, r12 = m.GetElement(1,0), m.GetElement(1,1), m.GetElement(1,2)
        r20, r21, r22 = m.GetElement(2,0), m.GetElement(2,1), m.GetElement(2,2)
        tr = r00 + r11 + r22
        if tr > 0:
            S = math.sqrt(tr + 1.0) * 2
            w = 0.25 * S
            x = (r21 - r12) / S
            y = (r02 - r20) / S
            z = (r10 - r01) / S
        elif (r00 > r11) and (r00 > r22):
            S = math.sqrt(1.0 + r00 - r11 - r22) * 2
            w = (r21 - r12) / S
            x = 0.25 * S
            y = (r01 + r10) / S
            z = (r02 + r20) / S
        elif r11 > r22:
            S = math.sqrt(1.0 + r11 - r00 - r22) * 2
            w = (r02 - r20) / S
            x = (r01 + r10) / S
            y = 0.25 * S
            z = (r12 + r21) / S
        else:
            S = math.sqrt(1.0 + r22 - r00 - r11) * 2
            w = (r10 - r01) / S
            x = (r02 + r20) / S
            y = (r12 + r21) / S
            z = 0.25 * S
        return (w, x, y, z)

    def _quat_angle_deg(self, q1, q2):
        # angle entre deux orientations (quaternions normalisés)
        w1,x1,y1,z1 = q1
        w2,x2,y2,z2 = q2
        dot = w1*w2 + x1*x2 + y1*y2 + z1*z2
        dot = max(-1.0, min(1.0, dot))
        # distance angulaire (radians), tenir compte du signe (q et -q sont identiques)
        angle = 2.0 * math.acos(abs(dot))
        return math.degrees(angle)


    # >>> NEW: callback bouton 3D (press/release)
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def _onVRButtonEvent(self, caller, event, calldata):
        ed = vtk.vtkEventData.SafeDownCast(calldata)
        if not ed or not isinstance(ed, vtk.vtkEventDataDevice3D):
            return
        device = int(ed.GetDevice())  # HMD/LeftController/RightController
        input_ = int(ed.GetInput())   # Trigger/Grip/TrackPad/Button…
        action = int(ed.GetAction())  # Press=1, Release=2, Touch=3, Untouch=4 (valeurs VTK)
        key = (device, input_)

        # Comptage press/release
        if action == vtk.vtkEventDataAction.Press:
            self.buttonPressCount += 1
            self._pressedState[key] = True
        elif action == vtk.vtkEventDataAction.Release:
            self.buttonReleaseCount += 1
            # interaction = appui→relâche sur la même (device,input)
            if self._pressedState.get(key):
                self.interactionCount += 1
            self._pressedState[key] = False

    # >>> NEW: callback mouvement HMD (intègre translation & rotation)
    def _onHMDTransformModified(self, caller, event):
         # On lit la pose depuis le node qui a changé (caller) sinon depuis le HMD connu
        node = caller if isinstance(caller, slicer.vtkMRMLTransformNode) else self._headTransformNode
        if not node:
            return

        m = vtk.vtkMatrix4x4()
        node.GetMatrixTransformToParent(m)

        # Position du point de vue VR = position du HMD
        pos = (m.GetElement(0,3), m.GetElement(1,3), m.GetElement(2,3))
        # Direction de visée "caméra" = -Z du repère HMD (forward)
        fwd = (-m.GetElement(0,2), -m.GetElement(1,2), -m.GetElement(2,2))

        # --- Translation cumulée (comme ton desktop) ---
        if self._vrCamLastPos is not None:
            dp = (pos[0]-self._vrCamLastPos[0], pos[1]-self._vrCamLastPos[1], pos[2]-self._vrCamLastPos[2])
            dmm = _norm(dp)
            if dmm > self._camEpsMm:
                self.vrCamTransMm += dmm
                self._vrCamLastPos = pos
        else:
            self._vrCamLastPos = pos

        # --- Rotation cumulée (changement d'axe de visée) ---
        if self._vrCamLastDir is not None:
            ddeg = _angle_deg(self._vrCamLastDir, fwd)
            if ddeg > self._camEpsDeg:
                self.vrCamRotDeg += ddeg
                self._vrCamLastDir = fwd
        else:
            self._vrCamLastDir = fwd

        # Alimente tes champs existants (affichage/CSV)
        self.hmdDistance_mm = self.vrCamTransMm
        self.hmdRotation_deg = self.vrCamRotDeg

    def onTractDisplay(self):
        if not hasattr(self, 'tractoDisplayWidget'):
            self.tractoDisplayWidget = slicer.modules.tractographydisplay.createNewWidgetRepresentation()
            self.tractoDisplayWidget.setWindowFlags(self.tractoDisplayWidget.windowFlags | Qt.Tool)

            class ClickOutsideFilter(QObject):
                def __init__(self, parentWidget):
                    super().__init__()
                    self.parentWidget = parentWidget

                def eventFilter(self, obj, event):
                    if event.type() == QEvent.MouseButtonPress:
                        if self.parentWidget.isVisible() and not self.parentWidget.geometry.contains(event.globalPos()):
                            self.parentWidget.close()
                    return False

            self._clickFilter = ClickOutsideFilter(self.tractoDisplayWidget)
            QApplication.instance().installEventFilter(self._clickFilter)

            self.tractoDisplayWidget.destroyed.connect(
                lambda: QApplication.instance().removeEventFilter(self._clickFilter)
            )


        self.tractoDisplayWidget.show()
        self.tractoDisplayWidget.raise_()


    #@vtk.calldata_type(vtk.VTK_OBJECT)
    def onStartVR(self, roiNode):
        self.defaultVRCamera = None
       
        if hasattr(slicer.modules, "virtualreality"):
            # Clean up any observers left over from a previous Start VR call (e.g. if "End Task"
            # was never clicked in between) so they cannot pile up and double-apply input.
            self._teardownVRSensors()

            vr = slicer.modules.virtualreality
            vr.logic().SetVirtualRealityConnected(True)
            vr.logic().SetVirtualRealityActive(True)
            vr.widgetRepresentation().setControllerTransformsUpdate(True)
            vr.widgetRepresentation().setHMDTransformUpdate(True)
            vr.viewWidget().setGrabObjectsEnabled(True)
            # vr.viewWidget().SetGestureButtonToGrip()
            print("VR works")

            self.disableSelection()
            self.startTime = time.perf_counter()
            self.updateClickCount = 0
            self.cubeMoveCount = 0
            self.timeRunning = True

            # Reset métriques à chaque session
            self.buttonPressCount = 0
            self.buttonReleaseCount = 0
            self.interactionCount = 0
            self._pressedState.clear()
            self.hmdDistance_mm = 0.0
            self.hmdRotation_deg = 0.0

            self.vrCamTransMm = 0.0
            self.vrCamRotDeg  = 0.0
            self._vrCamLastPos = None
            self._vrCamLastDir = None

            # >>> NEW: reset état rotation tourne-disque (stick gauche + modificateur)
            self._leftStickX = 0.0
            self._leftStickY = 0.0
            self._modifierHeld = False
            self._fiberPivotWorld = None
            self._lastRotationTickTime = None

            # Always register the Menu3DEvent observer first (works with any SlicerVR version).
            # The VTK OpenXR action manifest maps B -> "showmenu" -> Menu3DEvent regardless
            # of which SlicerVR build is installed, so this is the reliable base path.
            self.vrInteractor = vr.viewWidget().interactor()
            if self.vrInteractor:
                highPriority = 100.0
                self._hasPerControlEvents = False

                self._menuSuppressObserverTag = self.vrInteractor.AddObserver(
                    vtk.vtkCommand.Menu3DEvent, self._onBButtonMenuEvent, highPriority
                )
                self._buttonObserversIds.append(self._menuSuppressObserverTag)

                # Try to also bind the newer per-control OpenXR events exposed by
                # vtkVirtualRealityViewOpenXRInteractorStyle (Sunderlandkyl's SlicerVR build).
                # If unavailable (standard KitwareMedical release), the Menu3DEvent path above
                # already handles B and we skip this block gracefully.
                try:
                    import vtkSlicerVirtualRealityModuleMRMLDisplayableManagerPython as vtkSlicerVRMRMLDM
                    ControllerEvents = vtkSlicerVRMRMLDM.vtkVirtualRealityViewOpenXRInteractorStyle

                    # Right "A" button -> press/release/interaction counting
                    self._buttonObserversIds.append(
                        self.vrInteractor.AddObserver(ControllerEvents.RightButton1ClickEvent, self._onVRButtonEvent)
                    )

                    # Right "B" button -> Update Fiber (Menu3DEvent observer above suppresses
                    # the menu; this observer calls onSaveFiber via the per-control path)
                    self._updateFiberObserverTag = self.vrInteractor.AddObserver(
                        ControllerEvents.RightButton2ClickEvent, self._onUpdateFiberButtonEvent, highPriority
                    )
                    self._buttonObserversIds.append(self._updateFiberObserverTag)

                    # Left thumbstick -> cache stick position (fires only on value change)
                    self._leftStickObserverTag = self.vrInteractor.AddObserver(
                        ControllerEvents.LeftThumbstickEvent, self._onLeftStickEvent, highPriority
                    )
                    self._buttonObserversIds.append(self._leftStickObserverTag)

                    # Left aim pose -> per-frame tick to apply turntable rotation
                    self._buttonObserversIds.append(
                        self.vrInteractor.AddObserver(ControllerEvents.LeftAimPoseEvent, self._onLeftControllerMoveEvent)
                    )

                    # Left grip -> rotation modifier; AbortFlagOn prevents default prop-grab
                    self._rotationModifierObserverTag = self.vrInteractor.AddObserver(
                        ControllerEvents.LeftGripClickEvent, self._onRotationModifierEvent, highPriority
                    )
                    self._buttonObserversIds.append(self._rotationModifierObserverTag)

                    self._hasPerControlEvents = True
                    print("[VR] Per-control OpenXR events registered.")
                except Exception as e:
                    print(f"[VR] Per-control events unavailable (standard SlicerVR build): {e}")
                    print("[VR] B button handled via Menu3DEvent fallback.")

            self._initHMDTracking()

    def _attachHMDObserver(self, node):
        """Attache l'observer sur le node HMD et initialise les valeurs de départ."""
        self._headTransformNode = node

        m0 = vtk.vtkMatrix4x4()
        self._headTransformNode.GetMatrixTransformToParent(m0)
        self._lastHMDPos = [m0.GetElement(0,3), m0.GetElement(1,3), m0.GetElement(2,3)]
        self._lastHMDQuat = self._mat_to_quat(m0)

        # Seed de la caméra VR
        self._vrCamLastPos = [m0.GetElement(0,3), m0.GetElement(1,3), m0.GetElement(2,3)]
        self._vrCamLastDir = (-m0.GetElement(0,2), -m0.GetElement(1,2), -m0.GetElement(2,2))

        self._hmdObserverId = self._headTransformNode.AddObserver(
            slicer.vtkMRMLTransformNode.TransformModifiedEvent,
            self._onHMDTransformModified
        )
        print("[VR] HMD tracking initialisé.")


    def _initHMDTracking(self):
        """Essayez d'abord de récupérer directement le HMD, sinon écoute NodeAddedEvent."""
        # 1) Essai direct
        try:
            node = slicer.util.getNode("VirtualReality.HMD")
            self._attachHMDObserver(node)
            return
        except slicer.util.MRMLNodeNotFoundException:
            print("[VR] 'VirtualReality.HMD' pas encore créé, on attend NodeAddedEvent...")

        # 2) Si pas trouvé, on écoute la scène pour savoir quand il apparaît
        if self._sceneHMDObserverId is None:
            @vtk.calldata_type(vtk.VTK_OBJECT)
            def _onNodeAdded(caller, event, addedNode):
                if (isinstance(addedNode, slicer.vtkMRMLTransformNode)
                        and addedNode.GetName() == "VirtualReality.HMD"):
                    print("[VR] 'VirtualReality.HMD' détecté, on connecte l'observer.")
                    # On n'a plus besoin d'écouter la scène
                    slicer.mrmlScene.RemoveObserver(self._sceneHMDObserverId)
                    self._sceneHMDObserverId = None
                    self._attachHMDObserver(addedNode)

            self._sceneHMDObserverId = slicer.mrmlScene.AddObserver(
                slicer.vtkMRMLScene.NodeAddedEvent,
                _onNodeAdded
            )
        

    # >>> NEW: bouton "B" droit (right_button2_click) -> Update Fiber
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def _onUpdateFiberButtonEvent(self, caller, event, calldata):
        ed = vtk.vtkEventDataDevice3D.SafeDownCast(calldata)
        if not ed or ed.GetAction() != vtk.vtkEventDataAction.Press:
            return
        if hasattr(self, "efr"):
            self.onSaveFiber()
        else:
            print("[VR] 'B' pressed but no fiber bundle/ROI exists yet (use 'Create Cube' first).")

    # B button via showmenu -> Menu3DEvent. Always aborts the menu. When the per-control
    # OpenXR events are unavailable (standard SlicerVR build), also triggers Update Fiber.
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def _onBButtonMenuEvent(self, caller, event, calldata):
        caller.GetCommand(self._menuSuppressObserverTag).AbortFlagOn()
        if self._hasPerControlEvents:
            return  # RightButton2ClickEvent observer handles the update
        ed = vtk.vtkEventDataDevice3D.SafeDownCast(calldata)
        if not ed or ed.GetAction() != vtk.vtkEventDataAction.Press:
            return
        if hasattr(self, "efr"):
            self.onSaveFiber()
        else:
            print("[VR] 'B' pressed but no fiber bundle/ROI exists yet (use 'Create Cube' first).")

    # >>> NEW: stick gauche (left_thumbstick) -> met a jour la position memorisee du stick. Cet
    # evenement ne se declenche que lorsque la valeur change (pas a chaque frame), donc on ne
    # fait QUE mettre en cache ici; c'est LeftAimPoseEvent (ci-dessous), qui se declenche a chaque
    # frame, qui rejoue la rotation a partir de cette valeur memorisee.
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def _onLeftStickEvent(self, caller, event, calldata):
        ed = vtk.vtkEventDataDevice3D.SafeDownCast(calldata)
        if not ed:
            return
        pos = ed.GetTrackPadPosition()
        x, y = pos[0], pos[1]
        # Defensive sanity check: a real thumbstick sample is always within [-1, 1]. Reject
        # anything else (NaN/garbage) instead of caching it, in case some other action ever
        # ends up sharing this VTK event without actually writing a fresh position.
        if not (-1.0 <= x <= 1.0 and -1.0 <= y <= 1.0):
            return
        self._leftStickX, self._leftStickY = x, y

    # >>> NEW: pose du controleur gauche (left_aim_pose) -> se declenche a chaque frame tant
    # que le controleur est suivi, peu importe si le stick a change. On l'utilise comme "tick"
    # pour rejouer la rotation en continu a partir de la derniere position memorisee du stick.
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def _onLeftControllerMoveEvent(self, caller, event, calldata):
        self._applyTurntableRotation(self._leftStickX, self._leftStickY)

    # >>> NEW: grip gauche (left_grip_click) -> modificateur de mode de rotation (roulis)
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def _onRotationModifierEvent(self, caller, event, calldata):
        caller.GetCommand(self._rotationModifierObserverTag).AbortFlagOn()
        ed = vtk.vtkEventDataDevice3D.SafeDownCast(calldata)
        if not ed:
            return
        if ed.GetAction() == vtk.vtkEventDataAction.Press:
            self._modifierHeld = True
        elif ed.GetAction() == vtk.vtkEventDataAction.Release:
            self._modifierHeld = False

    # >>> NEW: centre (monde) du fiber bundle, utilisé comme pivot de rotation. Calculé une
    # fois puis mis en cache (le bundle n'est pas déplacé par la rotation, donc son centre
    # monde reste stable).
    def _getFiberPivotWorld(self):
        if self._fiberPivotWorld is not None:
            return self._fiberPivotWorld
        fbn = getattr(self, "fbn", None)
        if fbn is None:
            return None
        polyData = fbn.GetPolyData()
        if polyData is None or polyData.GetNumberOfPoints() == 0:
            return None
        center = polyData.GetCenter()
        self._fiberPivotWorld = (center[0], center[1], center[2])
        return self._fiberPivotWorld

    # >>> NEW: direction "devant" du HMD (monde), projetée à l'horizontale (composante
    # verticale retirée) pour ne pas dépendre de l'inclinaison de la tête.
    def _getHMDForwardHorizontal(self, up):
        node = self._headTransformNode
        if node is None:
            return None
        m = vtk.vtkMatrix4x4()
        node.GetMatrixTransformToParent(m)
        fwd = (-m.GetElement(0, 2), -m.GetElement(1, 2), -m.GetElement(2, 2))
        d = _dot(fwd, up)
        horiz = (fwd[0] - d * up[0], fwd[1] - d * up[1], fwd[2] - d * up[2])
        if _norm(horiz) < 1e-6:
            return None
        return _normalize(horiz)

    # >>> NEW: applique un increment de rotation tourne-disque pour un echantillon (x,y) du
    # stick gauche. On fait tourner le MONDE (PhysicalToWorldMatrix) autour du centre du fiber
    # bundle plutot que de modifier la position du bundle lui-meme, afin que le bundle et le
    # cube ROI restent alignes l'un par rapport a l'autre pendant qu'ils semblent tourner
    # ensemble pour l'utilisateur. L'increment est proportionnel au temps reel ecoule depuis le
    # dernier echantillon, pas a un pas fixe, donc la vitesse reste correcte quelle que soit la
    # fréquence des evenements.
    def _applyTurntableRotation(self, x, y):
        now = time.perf_counter()
        lastTime = self._lastRotationTickTime
        self._lastRotationTickTime = now
        if lastTime is None:
            # Premier echantillon depuis le debut/la reprise de la session: on amorce juste
            # l'horloge, sans appliquer de rotation (on ne connait pas encore le dt réel).
            return
        dt = now - lastTime
        if dt <= 0 or dt > 0.25:
            # Le stick etait au repos (ou la frequence des evenements a saute) depuis un moment:
            # on ignore cet intervalle plutot que d'integrer un dt perime/anormalement grand.
            return

        if abs(x) < VR_STICK_DEADZONE:
            x = 0.0
        if abs(y) < VR_STICK_DEADZONE:
            y = 0.0
        if x == 0.0 and y == 0.0:
            return

        if not hasattr(slicer.modules, "virtualreality"):
            return
        vr = slicer.modules.virtualreality
        renderWindow = vr.viewWidget().renderWindow()
        if renderWindow is None:
            return

        pivot = self._getFiberPivotWorld()
        if pivot is None:
            return

        up = _normalize(renderWindow.GetPhysicalViewUp())
        camFwdHoriz = self._getHMDForwardHorizontal(up)
        if camFwdHoriz is None:
            return
        right = _normalize(_cross(camFwdHoriz, up))

        # We orbit the camera/world about the pivot rather than rotating the bundle
        # directly, so every angle here is the negative of what a direct object-rotation
        # would use: orbiting the camera by +angle makes the world appear to turn by -angle.
        orbit = vtk.vtkTransform()
        orbit.PostMultiply()
        orbit.Translate(-pivot[0], -pivot[1], -pivot[2])

        if self._modifierHeld:
            # Left -> object appears to roll clockwise as the user faces it, about the
            # horizontal direction the user is currently looking.
            rollAngle = x * VR_ROLL_SPEED_DEG_PER_SEC * dt
            orbit.RotateWXYZ(rollAngle, camFwdHoriz[0], camFwdHoriz[1], camFwdHoriz[2])
        else:
            # Right -> turntable spins counterclockwise viewed from above (about real-world up).
            yawAngle = -x * VR_YAW_SPEED_DEG_PER_SEC * dt
            orbit.RotateWXYZ(yawAngle, up[0], up[1], up[2])
            # Stick pushed away from the user -> top of the object tilts away (about the
            # horizontal axis pointing to the user's right).
            pitchAngle = y * VR_PITCH_SPEED_DEG_PER_SEC * dt
            orbit.RotateWXYZ(pitchAngle, right[0], right[1], right[2])

        orbit.Translate(pivot[0], pivot[1], pivot[2])

        currentMatrix = vtk.vtkMatrix4x4()
        renderWindow.GetPhysicalToWorldMatrix(currentMatrix)
        newMatrix = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Multiply4x4(orbit.GetMatrix(), currentMatrix, newMatrix)
        renderWindow.SetPhysicalToWorldMatrix(newMatrix)

    def disableSelection(self):
        nodeClasses = ("vtkMRMLScalarVolumeNode", "vtkMRMLFiberBundleNode", "vtkMRMLSegmentationNode")
        for className in nodeClasses:
            nodes = slicer.util.getNodes(className + "*")
            for node in nodes.values():
                node.SetSelectable(0)

    def onCubeCreate(self):
        self.currentCubeSize = 10
        # Create cube
        self.cubeSource = vtk.vtkCubeSource()
        self.cubeSource.SetXLength(10)
        self.cubeSource.SetYLength(10)
        self.cubeSource.SetZLength(10)
        self.cubeSource.Update()

        # Add model to scene
        self.modelNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", "VR_Cube")
        self.modelNode.SetAndObservePolyData(self.cubeSource.GetOutput())
        modelDisplayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelDisplayNode")
        modelDisplayNode.SetColor(1, 0, 0)  # rouge
        modelDisplayNode.SetOpacity(0.3)
        modelDisplayNode.SetVisibility3D(True)
        self.modelNode.SetAndObserveDisplayNodeID(modelDisplayNode.GetID())
        self.cubeDisplayNode = modelDisplayNode  # >>> NEW: pour le flash blanc sur Update Fiber
        print("cube create")

        # create transform
        self.t  = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode", "center")
        mat = vtk.vtkMatrix4x4()
        mat.Identity()
        mat.SetElement(0, 3, 30)  # X
        mat.SetElement(1, 3, 40)  # Y
        mat.SetElement(2, 3, 50)  # Z
        self.t .SetMatrixTransformToParent(mat)
        self.modelNode.SetAndObserveTransformNodeID(self.t .GetID())

        # Get the fiber bundle node
        self.fbn = slicer.mrmlScene.GetNodesByClass("vtkMRMLFiberBundleNode").GetItemAsObject(0)
        self.efr = self.fbn.GetExtractFromROI()

        # Create implicit box
        self.boxFunc = vtk.vtkBox()
        self._updateBoxBounds()

        self.implicitBool = vtk.vtkImplicitBoolean()
        self.implicitBool.SetOperationTypeToIntersection()
        self.implicitBool.AddFunction(self.boxFunc)

        # Assign to ExtractFromROI
        self.efr.SetImplicitFunction(self.implicitBool)

        # négative selection
        self.efr.SetExtractInside(False)
        self.efr.SetExtractBoundaryCells(False)

        self.efr.Modified()
        self.fbn.SetSelectWithMarkups(True)
        self.efr.Update()

        self.t.AddObserver(
            slicer.vtkMRMLTransformNode.TransformModifiedEvent,
            self.onTransformModified
        )

        # self.fbn.AddObserver(
        #     vtk.vtkCommand.ModifiedEvent,
        #     lambda caller, evt: slicer.util.infoDisplay(
        #         f"{caller.GetFilteredPolyData().GetNumberOfLines()} remaining fibers"
        #     )
        # )

        self.ui.cubeButton.enabled = False
        self.ui.saveFiber.enabled = True
    
    def onTransformModified(self,caller, event):
        self.cubeMoveCount += 1
        mat = vtk.vtkMatrix4x4()
        caller.GetMatrixTransformToParent(mat)
        center = [mat.GetElement(0, 3), mat.GetElement(1, 3), mat.GetElement(2, 3)]

        halfSize = self.currentCubeSize / 2.0

        self.boxFunc.SetBounds(
            center[0] - halfSize, center[0] + halfSize,
            center[1] - halfSize, center[1] + halfSize,
            center[2] - halfSize, center[2] + halfSize,
        )
        self.efr.Modified()


    def _updateBoxBounds(self):
        mat = vtk.vtkMatrix4x4()
        self.t.GetMatrixTransformToParent(mat)
        halfSize = self.currentCubeSize / 2.0

        center = [mat.GetElement(0, 3), mat.GetElement(1, 3), mat.GetElement(2, 3)]

        self.boxFunc.SetBounds(
            center[0] - halfSize, center[0] + halfSize,
            center[1] - halfSize, center[1] + halfSize,
            center[2] - halfSize, center[2] + halfSize,
        )

    def onCubeSizeChanged(self, value):
        self.currentCubeSize = value
        self.cubeSource.SetXLength(value)
        self.cubeSource.SetYLength(value)
        self.cubeSource.SetZLength(value)
        self.cubeSource.Update()

        # update the model node
        self.modelNode.SetAndObservePolyData(self.cubeSource.GetOutput())
        self._updateBoxBounds()
        self.efr.Modified()


    # >>> NEW: flash bref (blanc) du cube ROI pour confirmer visuellement l'action Update Fiber
    def _flashCubeWhite(self, durationMs=200):
        displayNode = getattr(self, "cubeDisplayNode", None)
        if displayNode is None:
            return
        displayNode.SetColor(1, 1, 1)
        # Restaure toujours la couleur d'origine connue (rouge) plutot que de mémoriser la
        # couleur "actuelle", pour rester correct meme si Update Fiber est declenche plusieurs
        # fois rapidement (flashs qui se chevauchent).
        QTimer.singleShot(durationMs, lambda: displayNode.SetColor(1, 0, 0))

    def onSaveFiber(self):
        self._flashCubeWhite()
        self.updateClickCount += 1
        self.efr.Update()
        outputPD = self.efr.GetOutput()

        polyCopy = vtk.vtkPolyData()
        polyCopy.DeepCopy(outputPD)
        passThrough = vtk.vtkPassThrough()
        passThrough.SetInputData(polyCopy)
        passThrough.Update()

        self.fbn.SetMeshConnection(passThrough.GetOutputPort())
        self.efr = self.fbn.GetExtractFromROI()
        self.efr.SetImplicitFunction(self.implicitBool)
        self.efr.SetExtractInside(False)
        self.efr.SetExtractBoundaryCells(False)
        self.efr.Modified()

       # >>> NEW: nettoyage des observeurs VR
    def _teardownVRSensors(self):
        self._leftStickX = 0.0
        self._leftStickY = 0.0
        self._modifierHeld = False
        self._fiberPivotWorld = None
        self._lastRotationTickTime = None
        self._updateFiberObserverTag = None
        self._menuSuppressObserverTag = None
        self._hasPerControlEvents = False
        self._leftStickObserverTag = None
        self._rotationModifierObserverTag = None

        if self.vrInteractor and self._buttonObserversIds:
            for oid in self._buttonObserversIds:
                try:
                    self.vrInteractor.RemoveObserver(oid)
                except Exception:
                    pass
            self._buttonObserversIds.clear()
        if self._headTransformNode and self._hmdObserverId:
            try:
                self._headTransformNode.RemoveObserver(self._hmdObserverId)
            except Exception:
                pass
            self._hmdObserverId = None
        self._headTransformNode = None
        self.vrInteractor = None    

        if self._sceneHMDObserverId is not None:
            try:
                slicer.mrmlScene.RemoveObserver(self._sceneHMDObserverId)
            except Exception:
                pass
            self._sceneHMDObserverId = None
           
    def onEndTask(self):
        
            
        if self.timeRunning:
            self.endTime = time.perf_counter()
            self.timeRunning = False
            duration = self.endTime - self.startTime
            if self.fbn:
                numFibers = self.fbn.GetFilteredPolyData().GetNumberOfLines()
                print(f"Nombre de fibres restantes : {numFibers}")
            else:
                print("Aucune fibre chargée.")
                numFibers = -1

            print(f"Durée totale : {duration:.2f} secondes")
            print(f"Nombre de clics sur 'Update' : {self.updateClickCount}")
            print(f"Nombre de déplacements du cube : {self.cubeMoveCount}")

            # >>> NEW: print métriques VR
            print(f"[VR] Press: {self.buttonPressCount} | Release: {self.buttonReleaseCount} | Interactions: {self.interactionCount}")
            print(f"[VR] HMD distance: {self.hmdDistance_mm:.1f} mm | rotation: {self.hmdRotation_deg:.1f}°")

            self.saveResultsToFile(duration, self.updateClickCount, self.cubeMoveCount, numFibers,self.buttonPressCount,
                self.buttonReleaseCount,
                self.interactionCount,
                self.hmdDistance_mm,
                self.hmdRotation_deg)
        else:
            print("Le chronomètre n'était pas actif.")

        self._teardownVRSensors()

    def getLogFilePath(self):
        modulePath = os.path.dirname(__file__)
        logFolderPath = os.path.join(modulePath, "..", "Resources", "Logs")
        os.makedirs(logFolderPath, exist_ok=True)
        logFilePath = os.path.join(logFolderPath, "tractvrV1_log.csv")
        return logFilePath

    # def saveResultsToFile(self, duration, updateClicks, moveCount, numFibers):
    #     logFile = self.getLogFilePath()
    #     fileExists = os.path.isfile(logFile)
    #     timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    #     with open(logFile, mode="a", newline="") as f:
    #         writer = csv.writer(f)
    #         if not fileExists:
    #             writer.writerow(["Horodatage", "Durée (s)", "Nb Update", "Nb Déplacements Cube", "Fibres restantes"])
    #         writer.writerow([timestamp, f"{duration:.2f}", updateClicks, moveCount, numFibers])
    #         print(f"[DEBUG] Chemin de fichier : {logFile}")

    def saveResultsToFile(self, duration, updateClicks, moveCount, numFibers,
                          vrPress, vrRelease, vrInteractions, hmdDistMM, hmdRotDeg):
        logFile = self.getLogFilePath()
        fileExists = os.path.isfile(logFile)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(logFile, mode="a", newline="") as f:
            writer = csv.writer(f)
            if not fileExists:
                writer.writerow([
                    "Horodatage",
                    "Durée (s)",
                    "Nb Update",
                    "Nb Déplacements Cube",
                    "Fibres restantes",
                    "VR Press",
                    "VR Release",
                    "VR Interactions",
                    "HMD distance (mm)",
                    "HMD rotation (deg)"
                ])
            writer.writerow([
                timestamp,
                f"{duration:.2f}",
                updateClicks,
                moveCount,
                numFibers,
                vrPress,
                vrRelease,
                vrInteractions,
                f"{hmdDistMM:.1f}",
                f"{hmdRotDeg:.1f}"
            ])
            print(f"[DEBUG] Chemin de fichier : {logFile}")




#
# TractVRTest
#


class TractVRV1Test(ScriptedLoadableModuleTest):
    """
    This is the test case for your scripted module.
    Uses ScriptedLoadableModuleTest base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def setUp(self):
        """Do whatever is needed to reset the state - typically a scene clear will be enough."""
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here."""
        self.setUp()
        self.test_TractVRV1()

    def test_TractVRV1(self):
        """Ideally you should have several levels of tests.  At the lowest level
        tests should exercise the functionality of the logic with different inputs
        (both valid and invalid).  At higher levels your tests should emulate the
        way the user would interact with your code and confirm that it still works
        the way you intended.
        One of the most important features of the tests is that it should alert other
        developers when their changes will have an impact on the behavior of your
        module.  For example, if a developer removes a feature that you depend on,
        your test should break so they know that the feature is needed.
        """

        self.delayDisplay("Starting the test")

        # Get/create input data

        import SampleData

        # registerSampleData()
        # inputVolume = SampleData.downloadSample("TractVR1")
        self.delayDisplay("Loaded test data set")

        # inputScalarRange = inputVolume.GetImageData().GetScalarRange()
        # self.assertEqual(inputScalarRange[0], 0)
        # self.assertEqual(inputScalarRange[1], 695)

        outputVolume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
        threshold = 100

        # Test the module logic

        logic = TractVRV1Logic()

        # Test algorithm with non-inverted threshold
        # logic.process(inputVolume, outputVolume, threshold, True)
        # outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        # self.assertEqual(outputScalarRange[0], inputScalarRange[0])
        # self.assertEqual(outputScalarRange[1], threshold)

        # # Test algorithm with inverted threshold
        # logic.process(inputVolume, outputVolume, threshold, False)
        # outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        # self.assertEqual(outputScalarRange[0], inputScalarRange[0])
        # self.assertEqual(outputScalarRange[1], inputScalarRange[1])

        self.delayDisplay("Test passed")
