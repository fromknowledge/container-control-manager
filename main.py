import os
import time
import docker
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- Configuration ---
CONTAINER_NAME = "my-trading-bot-managed"
IMAGE_NAME = "trading-bot" # The tag for the image
HOST_DATA_PATH = os.path.abspath("./app/trading_data")
CONTAINER_DATA_PATH = "/app/data"
# --- NEW ---: Path to the directory containing your Dockerfile
# This assumes you run the API from your project's root folder.
BUILD_CONTEXT_PATH = os.path.abspath(".") 


# --- FastAPI and Docker Client Initialization ---
app = FastAPI(title="Docker Bot Manager API")
try:
    client = docker.from_env()
    client.ping()
    print("Successfully connected to Docker daemon.")
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    client = None

# --- Pydantic Model for Request Body ---
class DataUpdateRequest(BaseModel):
    filename: str
    content: str

# --- Existing API Endpoints (no changes needed here) ---

@app.get("/status", summary="Check the container's status")
def get_status():
    if not client: raise HTTPException(status_code=503, detail="Docker daemon is not available.")
    try:
        container = client.containers.get(CONTAINER_NAME)
        return {"container_name": container.name, "status": container.status}
    except docker.errors.NotFound:
        return {"status": "not_found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/start", summary="Create and start the container with status check")
def start_container():
    if not client: raise HTTPException(status_code=503, detail="Docker daemon is not available.")
    try:
        container = client.containers.get(CONTAINER_NAME)
        if container.status == "running": return {"status": "already_running"}
    except docker.errors.NotFound: pass
    print(f"Starting container '{CONTAINER_NAME}' from image '{IMAGE_NAME}'...")
    try:
        container = client.containers.run(
            image=IMAGE_NAME, name=CONTAINER_NAME, detach=True,
            volumes={HOST_DATA_PATH: {'bind': CONTAINER_DATA_PATH, 'mode': 'rw'}}
        )
        for _ in range(10):
            time.sleep(1); container.reload()
            if container.status == 'running': return {"status": "started_and_running"}
            if container.status in ['exited', 'dead']:
                logs = container.logs().decode('utf-8'); raise HTTPException(status_code=500, detail={"status": "container_failed_to_start", "logs": logs})
        raise HTTPException(status_code=504, detail="Container start timed out.")
    except docker.errors.ImageNotFound: raise HTTPException(status_code=404, detail=f"Image '{IMAGE_NAME}' not found. Please build it first.")
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@app.post("/stop", summary="Stop the container with status check")
def stop_container():
    if not client: raise HTTPException(status_code=503, detail="Docker daemon is not available.")
    try:
        container = client.containers.get(CONTAINER_NAME)
        if container.status == 'exited': return {"status": "already_stopped"}
        container.stop()
        for _ in range(10):
            container.reload()
            if container.status == 'exited': return {"status": "stopped_and_verified"}
            time.sleep(1)
        raise HTTPException(status_code=504, detail="Container stop timed out.")
    except docker.errors.NotFound: return {"status": "not_found_or_already_stopped"}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/restart", summary="Restart the container with status check")
def restart_container():
    if not client: raise HTTPException(status_code=503, detail="Docker daemon is not available.")
    try:
        container = client.containers.get(CONTAINER_NAME); container.restart()
        for _ in range(10):
            time.sleep(1); container.reload()
            if container.status == 'running': return {"status": "restarted_and_running"}
            if container.status in ['exited', 'dead']:
                logs = container.logs().decode('utf-8'); raise HTTPException(status_code=500, detail={"status": "container_failed_on_restart", "logs": logs})
        raise HTTPException(status_code=504, detail="Container restart timed out.")
    except docker.errors.NotFound: raise HTTPException(status_code=404, detail="Container not found.")
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-data", summary="Update a file and restart the container securely")
def update_data(request: DataUpdateRequest):
    print("--- Starting data update process ---"); stop_container()
    file_path = os.path.join(HOST_DATA_PATH, request.filename)
    try:
        with open(file_path, 'w') as f: f.write(request.content)
        print(f"Successfully updated data in {file_path}")
    except IOError as e:
        print("File write failed. Attempting to restart container anyway..."); restart_container()
        raise HTTPException(status_code=500, detail=f"Failed to write to file: {e}")
    restart_container()
    print("--- Data update process completed successfully ---")
    return {"status": "data_updated_and_restarted", "filename": request.filename}

# --- NEW /rebuild Endpoint ---
@app.post("/rebuild", summary="Rebuild image and redeploy container")
def rebuild_and_redeploy():
    """
    Automates the full update cycle:
    1. Stops and removes the current container.
    2. Rebuilds the Docker image from the Dockerfile.
    3. Starts a new container from the new image.
    """
    if not client:
        raise HTTPException(status_code=503, detail="Docker daemon is not available.")

    # --- Step 1: Stop and Remove Existing Container ---
    print("--- Starting rebuild process: Stopping and removing old container... ---")
    try:
        container = client.containers.get(CONTAINER_NAME)
        print(f"Found existing container '{CONTAINER_NAME}'. Stopping...")
        container.stop()
        print("Removing container...")
        container.remove()
        print("Old container removed.")
    except docker.errors.NotFound:
        print("No existing container found. Proceeding to build.")
        pass # It's okay if it doesn't exist, we just want it gone.
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error removing old container: {e}")

    # --- Step 2: Build the New Image ---
    print(f"--- Building image '{IMAGE_NAME}' from path '{BUILD_CONTEXT_PATH}'... ---")
    build_logs = ""
    try:
        # The build process returns the image object and a log generator
        _, logs_generator = client.images.build(
            path=BUILD_CONTEXT_PATH,
            tag=IMAGE_NAME,
            rm=True # Remove intermediate containers
        )
        
        # Stream logs from the generator and save them
        for chunk in logs_generator:
            if 'stream' in chunk:
                line = chunk['stream'].strip()
                print(line) # Print to server console in real-time
                build_logs += line + "\n"

    except docker.errors.BuildError as e:
        print(f"--- Build failed! ---")
        # The exception 'e' contains the build logs on failure
        return HTTPException(status_code=500, detail={"status": "build_failed", "logs": e.msg})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during build: {e}")
    
    print("--- Build successful! ---")

    # --- Step 3: Start the New Container ---
    print("--- Starting new container... ---")
    # We can reuse our robust start_container function
    start_response = start_container()

    return {
        "status": "rebuild_and_restart_successful",
        "final_container_status": start_response,
        "build_logs": build_logs,
    }