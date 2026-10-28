# Ruskomponen BOT BLK - Complete Documentation

## 📚 Overview

Ruskomponen BOT BLK adalah aplikasi desktop berbasis PyQt5 untuk mengontrol robot manipulator RNV3. Aplikasi ini menyediakan interface grafis modern dengan dark theme untuk mengelola gerakan robot, menyimpan/memuat program, mengontrol gripper, dan berkomunikasi dengan hardware robot melalui koneksi serial USB.

**Project:** Robot Control Interface  
**Version:** 1.0  
**Platform:** Windows (7/8/10/11)  
**Language:** Python + PyQt5  
**Last Updated:** May 28, 2026  

---

## 🎯 Core Features

### 1. Connection & Communication
- Auto-detect available COM ports
- Manual port selection
- 115200 baud serial communication
- Real-time status monitoring
- Automatic reconnection on loss

### 2. Motion Control (4-Axis)
- X, Y, Z, E axis control
- Spinbox & slider input methods
- Adjustable feed rate (1-5000 mm/min)
- Position feedback & display
- Range validation & boundary checking

### 3. Program Management
- Create motion programs by teaching waypoints
- Edit existing waypoints
- Save/load programs to file
- Reorder movements with up/down buttons
- Clear all movements
- Execute step-by-step or automatic

### 4. Gripper & Actuators
- Vacuum gripper ON/OFF
- Servo motor angle control (0-180°)
- Light gates (LG1, LG2, LG3) control
- Save gripper configurations

### 5. Calibration & Safety
- Home position setup
- Endstop verification
- Motor enable/disable
- Emergency stop (immediate halt)
- Position feedback reading

### 6. Monitoring & Diagnostics
- Real-time G-code logging
- Status display & indicators
- Complete command history
- Error messages & notifications

---

## 🛠️ Technical Specifications

### Hardware Interface
- **Communication:** Serial/USB (QSerialPort)
- **Protocol:** G-code commands
- **Baud Rate:** 115200
- **Data Format:** Text commands with CR/LF

### G-Code Command Format

```
Movement:   G0 X{x} Y{y} Z{z} E{e} F{speed}
Servo:      G100 A{angle} B{angle}

Special:
- G28: Home position
- M119: Check endstops
- M17: Enable motors
- M18: Disable motors

Gripper:
- VACUUM ON/OFF
- LG1/LG2/LG3 ON/OFF
```

### UI Specifications
- **Window Size:** 1427 x 904 pixels
- **Theme:** Dark mode (#2B2B2B background)
- **Text Color:** #ECEFF4 (light gray), #00FF00 (status green)
- **Accent Color:** #81A1C1 (blue-gray)

### Performance Targets
| Metric | Target |
|--------|--------|
| UI Response | <50ms |
| Serial Send | <10ms |
| Startup Time | <1 second |
| Memory Usage | <100MB |
| Position Accuracy | ±0.5mm |

---

## 📦 Dependencies

### Core Dependencies
```
PyQt5==5.15.9              # GUI Framework
PyQt5-sip==12.13.0        # Python/Qt bindings
pyserial==3.5             # Serial communication
```

### Python Standard Libraries (included)
- sys, time, asyncio, threading, re, ctypes

### Windows-Specific
- dwmapi.dll (Dark mode title bar)

---

## 🔌 Serial Communication Protocol

### Connection Parameters
| Parameter | Value |
|-----------|-------|
| Baud Rate | 115200 |
| Data Bits | 8 |
| Stop Bits | 1 |
| Parity | None |
| Flow Control | None |
| Encoding | ASCII/UTF-8 |
| Line Ending | CR LF (\r\n) |

### Command Response Format
```
ACK         → Command accepted
OK          → Command executed
ER          → Error occurred
POS X,Y,Z,E → Position feedback
STAT        → Status update
```

---

## 🚀 Setup Instructions

### 1. Prerequisites
```
Windows 7/8/10/11
Python 3.7 or higher
Git (for cloning)
```

### 2. Installation

```bash
# Clone repository
git clone https://github.com/februandi/SourceCodeInterface-RNV3.git
cd SourceCodeInterface-RNV3

# Create virtual environment
python -m venv venv

# Activate (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Application

```bash
python Ruskomponen_BOT_BLK.py
```

---

## 📋 Use Cases

### UC-1: Connect Robot
1. Launch application
2. Select COM port
3. Click "Sambungkan" (Connect)
4. Status label turns green "TERHUBUNG"

### UC-2: Calibrate
1. Verify robot at safe position
2. Click "Kalibrasi" (Calibration)
3. Robot moves to home (X=0, Y=217, Z=138)
4. Status becomes "SIAP" (Ready)

### UC-3: Manual Movement
1. Adjust spinbox or slider for each axis
2. System generates G-code automatically
3. Robot executes movement
4. Position updates in real-time

### UC-4: Create Program
1. Teach waypoints by manual positioning
2. Click "Simpan Gerakan" after each position
3. Edit/reorder waypoints as needed
4. Click "Simpan Sebagai" to save file

### UC-5: Execute Program
1. Open saved program file
2. Verify sequence in list
3. Click "Mulai" (Start) to execute
4. Use Pause/Stop for control

### UC-6: Gripper Control
1. Click "Vacuum ON/OFF" for pneumatic gripper
2. Adjust servo angle spinbox (0-180°)
3. Control light gates (LG1/LG2/LG3)
4. Save configurations to program

---

## 🐛 Error Handling

### Critical Errors (Block Operation)
- Serial port not found
- Connection timeout
- Robot not responding
- Hardware malfunction

### Warning Errors (Log & Continue)
- Out-of-range values
- Endstop triggered
- Slow response time

### Recoverable Errors (Skip Command)
- Invalid G-code format
- Missing parameters
- Parse errors

---

## 📊 File Structure

```
SourceCodeInterface-RNV3/
├── Ruskomponen_BOT_BLK.py       # Main application (5389 lines)
├── requirements.txt              # Python dependencies
├── README.md                     # Quick start guide
├── DOCUMENTATION.md             # This file
├── .gitignore                   # Git ignore rules
├── ruskomponen_bot.ui          # PyQt UI design
├── logo.png                     # Application icon
├── venv/                        # Virtual environment (not in git)
│   ├── Scripts/
│   ├── Lib/
│   └── pyvenv.cfg
└── robotRNV3_v2_01/           # Arduino firmware
    ├── robotRNV3_v2_01.ino
    ├── command.cpp/.h
    ├── endstop.cpp/.h
    ├── equipment.cpp/.h
    ├── fanControl.cpp/.h
    ├── interpolation.cpp/.h
    ├── logger.cpp/.h
    ├── queue.h
    ├── RampsStepper.cpp/.h
    ├── robotGeometry.cpp/.h
    └── pinout/
        └── pinout.h
```

---

## 🧪 Testing Strategy

### Unit Tests
- G-code parser validation
- Input boundary checking
- Serial protocol formatting
- State machine transitions

### Integration Tests
- End-to-end motion sequences
- Program save/load
- Connection recovery
- Button event handling

### System Tests
- Long-duration stability (8+ hours)
- Stress testing (100+ waypoints)
- Error recovery scenarios
- UI responsiveness

---

## 🔒 Security & Safety

### Safety Features
- Max velocity: 5000 mm/min
- Emergency stop: immediate halt
- Limit switch validation
- Calibration requirement
- Command validation

### Software Safety
- Boundary checking on all inputs
- Safe default values
- Graceful error handling
- Watchdog timer protection

---

## 🔄 Version Control

**Repository:** https://github.com/februandi/SourceCodeInterface-RNV3.git  
**Branch:** main  
**Latest Commit:** Add .gitignore and README.md  

### Ignored Files/Folders
```
venv/
__pycache__/
*.pyc
.DS_Store
*.log
```

---

## 🚧 Future Enhancements

### Short Term
- Advanced motion profiles
- Program templates library
- Better trajectory planning
- Enhanced error recovery

### Medium Term
- Multi-robot support
- Simulation/visualization
- Remote monitoring
- Data logging/analytics

### Long Term
- Collision detection
- Path optimization
- Web dashboard
- Mobile app interface

---

## 📝 Development Notes

### Code Structure
- **UI Class:** Ui_RUSKOMPONEN_BOT (PyQt Designer output)
- **Main Functions:** 50+ methods for various operations
- **Custom Dialogs:** GripperChoiceDialog, GripperChoiceDialog_LG
- **Threading:** Background serial communication
- **Timers:** QTimer for blinking UI feedback

### Deprecation Warnings (Non-Critical)
```
sipPyTypeDict() deprecation - PyQt5-sip issue
QFont::setPointSize: Point size <= 0 - Font configuration
```

These warnings don't affect functionality.

---

## 📞 Support & Contact

For issues or questions, refer to:
1. README.md - Quick start
2. REQUIREMENTS.md - Detailed specifications
3. TECHNICAL_SPECIFICATION.md - Architecture & protocol
4. USE_CASES_USER_STORIES.md - Use cases & workflows

---

**Project Status:** ✅ Complete & Production Ready

**Last Updated:** May 28, 2026  
**Version:** 1.0  
**Maintained By:** Ruskomponen Team  

---
