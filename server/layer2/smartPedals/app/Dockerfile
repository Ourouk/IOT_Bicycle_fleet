# Use an Python image based on alpine
FROM python:3.13-alpine

# Set the working directory inside the container
WORKDIR /app

# Copy inside the requirements.txt file and the application
COPY ["requirements.txt", "app.py", "/app"]
COPY ["templates", "/app/templates"]

# Install the needed packages specified inside the requirements.txt file
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port 5000 (flask) & 8443 tls -> it's a metadata information (no mapping)
EXPOSE 5000
EXPOSE 8443

# Define environment variable
ENV FLASK_APP=app.py

# Run the application (--host=0.0.0.0 ensure that the server is accessible from outside)
ENTRYPOINT ["python3"]
CMD ["app.py"]
