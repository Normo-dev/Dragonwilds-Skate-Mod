# Controls

There are two separate actions: **enter/exit skateboard mode**, and **get on/off the board inside that mode**. You can walk or run carrying the board without returning to ordinary Dragonwilds movement.

## Enter and leave skateboard mode

Equip **Skateboard** in **Mounts**, close the menu, then **hold your normal mount control for a moment** (about a quarter of a second; default keyboard **Alt**). Hold it again to exit skateboard mode. **F8** is a fallback toggle with the same entry checks.

The controller mount control follows your Dragonwilds binding. If you bind mounting to **Y/Triangle**, a quick tap changes board state and a hold leaves skateboard mode.

## Riding and carrying

These are the default keyboard mappings. Controller button names below describe the same source actions.

| Action | Keyboard | Xbox | PlayStation |
| --- | --- | --- | --- |
| Move / steer | W A S D | Left stick | Left stick |
| Push with right foot | Space | A | Cross |
| Push with left foot | Left Shift | X | Square |
| Brake while riding | Left Ctrl | B | Circle |
| Ollie / flick tricks | Arrow-key gestures | Right-stick gestures | Right-stick gestures |
| Get on/off board, remain in skateboard mode | R | Tap Y | Tap Triangle |
| Left / right grab | Q / E | LT / RT | L2 / R2 |
| Left / right bumper action | Z / C | LB / RB | L1 / R1 |
| Run while carrying board | Hold Space while moving | Hold A while moving | Hold Cross while moving |
| Jump while off the board | Left Shift | X | Square |
| Cycle camera | V | Right-stick click | R3 |

Triggers and face buttons also have contextual actions in the imported simulation. For example, triggers can drop, throw or retrieve the board while off-board. Z/C expose the source bumper actions; they are not dedicated mod grind buttons.

**Mouse:** there are no additional skating bindings assigned to mouse buttons or mouse movement. Use the movement/trick controls and automatic skating cameras. Ordinary Dragonwilds menus retain their normal mouse controls.

## First ride

1. Enter skateboard mode using the mount control and let the throw-down finish.
2. Push with **Space / A / Cross** and steer with **A/D or the left stick**.
3. For an ollie, pull the **right stick down, then quickly up**. On keyboard, press **Down**, release it, then quickly press **Up**. Opposite arrows held together cancel each other.
4. Approach a supported edge and land aligned with it to grind. Recognition uses the imported simulation; no separate grind key forces attachment.
5. Brake with **Left Ctrl / B / Circle**.
6. Tap **R / Y / Triangle** to step off and carry the board. Hold the normal mount control to return to ordinary Dragonwilds play.

Keyboard arrows emulate a digital right stick. Analog gestures, manuals and advanced trick combinations are easier to control with a controller. Tricks depend on stance, timing and the original simulation state; this guide does not promise every combination has passed a live test.

## Cameras

**V / right-stick click** cycles:

1. **High:** elevated skating view.
2. **Low:** lower skating view.
3. **Dragonwilds:** uses the captured Dragonwilds camera placement with the skating camera's movement-following rotation.

Right-stick click is reserved for this camera cycle. It is not forwarded as a source trick button.

## Menus and controllers

The spell wheel and inventory radial are blocked during skateboard mode so **R** and controller wheel buttons can serve the skating controls. Both wheels return after exiting the mode. Opening ordinary menus suspends skating input; close them before continuing.

Native SDL controller input is included for Xbox and supported PlayStation devices, including a direct DualSense path. DS4Windows is not required by the adapter. For a direct-input test, connect one controller and avoid a simultaneous virtual duplicate. Steam Input or another remapper may expose a different device. Wired and Bluetooth combinations still need final acceptance testing; advanced DualSense features are not promised.

## Changing keyboard bindings

Close Dragonwilds first. The advanced binding file is `RSDragonwilds/Binaries/Win64/ue4ss/Mods/DragonwildsSkate/Scripts/skate_bindings.lua` inside the game folder. Back it up before editing. Use Unreal key names and avoid mapping the mount action onto another held skating action. A graphical keybinding editor is not included in this candidate.

Keep the original backup. Setup refuses to overwrite modified installed files, so restore the original binding file before updating the mod. After the update, reapply your preferred bindings to the new file.

**F10 is a developer reload key**, not a required gameplay control. Do not repeatedly reload while collision work is running. Use the setup repair steps if a wait is stuck.
