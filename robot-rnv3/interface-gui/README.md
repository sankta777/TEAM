# Ruskomponen BOT BLK - Robot Control Interface

Aplikasi desktop PyQt5 untuk mengontrol robot manipulator RNV3 dengan antarmuka grafis modern.

## 📋 Dokumentasi Lengkap

Dokumentasi lengkap tersedia di folder root `docs/`:
- [REQUIREMENTS.md](../REQUIREMENTS.md) - Spesifikasi lengkap dan fitur
- [TECHNICAL_SPECIFICATION.md](../TECHNICAL_SPECIFICATION.md) - Arsitektur teknis dan protocol
- [USE_CASES_USER_STORIES.md](../USE_CASES_USER_STORIES.md) - Use cases dan user stories
- [README_DOCUMENTATION.md](../README_DOCUMENTATION.md) - Executive summary

## 🚀 Quick Start

### Prerequisites
- Python 3.7+
- Windows 7/8/10/11

### Setup

```bash
# 1. Clone repository
git clone https://github.com/februandi/SourceCodeInterface-RNV3.git
cd SourceCodeInterface-RNV3

# 2. Create virtual environment
python -m venv venv

# 3. Activate virtual environment
.\venv\Scripts\Activate.ps1

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run application
python Ruskomponen_BOT_BLK.py
```

## 📦 Dependencies

- **PyQt5** (5.15.9) - GUI Framework
- **PyQt5-sip** (12.13.0) - Python/Qt bindings
- **pyserial** (3.5) - Serial communication

## 🔧 Project Structure

```
SourceCodeInterface-RNV3/
├── venv/                           # Virtual environment (ignored)
├── Ruskomponen_BOT_BLK.py         # Main application
├── requirements.txt                # Python dependencies
├── ruskomponen_bot.ui             # PyQt UI design file
├── logo.png                        # Application icon
├── .gitignore                      # Git ignore rules
├── README.md                       # This file
└── robotRNV3_v2_01/              # Arduino firmware files
```

## 🎯 Key Features

- ✅ 4-Axis Motion Control (X, Y, Z, E)
- ✅ Serial/USB Communication (115200 baud)
- ✅ Motion Program Creation & Execution
- ✅ Gripper & Actuator Control
- ✅ Robot Calibration & Home Position
- ✅ Real-time Status Monitoring
- ✅ Dark Theme UI
- ✅ G-Code Command Generation

## 🖥️ System Requirements

| Component | Requirement |
|-----------|-------------|
| OS | Windows 7/8/10/11 |
| Python | 3.7+ |
| RAM | 512MB minimum, 2GB recommended |
| Storage | 200MB+ |

## 📝 Serial Communication Protocol

**Baud Rate:** 115200  
**Data Bits:** 8  
**Stop Bits:** 1  
**Parity:** None  

### G-Code Commands

```
# Movement
G0 X100 Y217 Z138 E0 F1000

# Servo Control
G100 A90 B45

# Gripper
VACUUM ON / VACUUM OFF
LG1 ON / LG1 OFF
LG2 ON / LG2 OFF
LG3 ON / LG3 OFF
```

## 🐛 Troubleshooting

### Application won't start
- Verify Python 3.7+ is installed
- Check all dependencies: `pip install -r requirements.txt`
- Ensure logo.png exists in the application directory

### Serial connection failed
- Verify USB cable is connected
- Check COM port in application
- Ensure robot controller is powered on
- Try different USB port

### Import errors
- Activate virtual environment: `.\venv\Scripts\Activate.ps1`
- Reinstall dependencies: `pip install -r requirements.txt --force-reinstall`

## 📞 Support

For detailed documentation, see the documentation files in the project root directory.

## 📄 License

Private Project - Ruskomponen Team

## ✅ Version

**Version:** 1.0  
**Last Updated:** May 28, 2026

---

**Happy coding! 🚀**
