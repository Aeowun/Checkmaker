# CheckMaker

A Markdown checklist editor for Windows.

## What is it?

CheckMaker is a tool for toggling checkboxes in Markdown files. It changes only the checkbox marker character on disk at its exact byte offset. This leaves the rest of the document's encoding, line endings, and formatting unchanged.

## How to use it

### Requirements
*   Windows 10 or 11.
*   (For source) Python 3.10 or newer.

### Installation
Download `CheckMaker.exe` from the latest release and run it.

### Usage
1.  Open a Markdown file.
2.  Click any checkbox to toggle its state. The change is applied immediately to the file on disk.

### Build from source
1.  Install dependencies: `pip install -r requirements.txt`
2.  Run the application: `python checkmaker.py`
3.  Build the executable: `pip install -r requirements-dev.txt` then `python build.py`
