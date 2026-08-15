#!/bin/bash

# Navigate to the folder
cd /home/tadinada/now-playing

# Activate the virtual environment
source .venv/bin/activate

# Execute the python script using 'exec' (CRITICAL)
exec python main.py