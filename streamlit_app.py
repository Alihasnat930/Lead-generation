"""Hosted entrypoint. Local run_app.bat continues to use app.py."""
import os
from pathlib import Path
import runpy
import streamlit as st

# Streamlit loads root secrets into the environment when st.secrets is accessed.
try:
    dict(st.secrets)
except FileNotFoundError:
    pass
os.environ['APP_ENV']='production'
runpy.run_path(str(Path(__file__).with_name('app.py')),run_name='__main__')
