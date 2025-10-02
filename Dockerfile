# Use an official lightweight Python image as a parent image
FROM python:3.10-slim

# Set the working directory in the container to /app
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the main application script into the container at /app
COPY ./app .

# Define the command to run your app when the container starts
CMD ["python", "-u", "open_source_setup.py"]