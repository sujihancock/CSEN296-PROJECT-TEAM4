---

## Quick Start Guide

### Prerequisites
- Python 3.9+

### Using `start.sh`

A convenient unified runner script is included:

```bash
# Make sure the script is executable
chmod +x start.sh

# 1. Start Both Backend (8000) & Frontend (3000) concurrently
./start.sh

# 2. Start only the Backend API
./start.sh backend

# 3. Start only the Frontend UI
./start.sh frontend

# 4. Run automated test suite
./start.sh test
```

### Accessing the Application
- **Frontend Dashboard**: [http://localhost:3000](http://localhost:3000)
- **Backend API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---