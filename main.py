import os
import yaml
import logging
import signal
import sys
import subprocess
from dotenv import load_dotenv
from commlib.node import Node
from commlib.transports.redis import ConnectionParameters
from commlib.pubsub import PubSubMessage
from commlib.rpc import RPCMessage  # Note we import RPCMessage directly now
import time
import threading

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# VersionMessage class for publish/subscribe communications
class VersionMessage(PubSubMessage):
    appname: str
    version_number: str
    dependencies: dict

# RPC message classes - now properly inheriting from RPCMessage base classes
class DockerCommandRequest(RPCMessage):
    command: str
    directory: str
    new_version: str = None

class DockerCommandResponse(RPCMessage):
    success: bool
    message: str

def load_docker_compose_data(directory='.', filename='docker-compose.yml'):
    """Reads appname and version_number from the image string in docker-compose.yml."""
    file_path = os.path.join(os.path.abspath(directory), filename)
    logging.info(f"Looking for docker-compose file at: {file_path}")
    
    try:
        with open(file_path, 'r') as stream:
            compose_data = yaml.safe_load(stream)
            if not compose_data or 'services' not in compose_data:
                raise ValueError(f"Invalid docker-compose file: {file_path}")
            
            service_name = list(compose_data['services'].keys())[0]
            image = compose_data['services'][service_name]['image']
            if '/' not in image or ':' not in image:
                raise ValueError(f"Invalid image format in {file_path}: {image}")
                
            appname_with_repo = image.split('/')[1]
            appname, version_number = appname_with_repo.split(':')
            return appname, version_number
    except FileNotFoundError:
        logging.error(f"Docker compose file not found: {file_path}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error reading {file_path}: {e}")
        sys.exit(1)

def publish_version(channel, appname, version_number, redis_ip, dependencies=None):
    """Publishes a version message to a specified Redis channel."""
    redis_host = os.getenv('REDIS_HOST', redis_ip)
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    redis_db = int(os.getenv('REDIS_DB', 0))

    conn_params = ConnectionParameters(
        host=redis_host,
        port=redis_port,
        db=redis_db
    )

    from commlib.transports.redis import Publisher
    publisher = Publisher(
        conn_params=conn_params,
        topic=channel,
        msg_type=VersionMessage
    )

    message = VersionMessage(appname=appname, version_number=version_number, dependencies=dependencies or {})
    publisher.publish(message)

    logging.info(f'Published version {version_number} of app {appname} to channel {channel}')
    if dependencies:
        for dep_app, dep_version in dependencies.items():
            logging.info(f'  Dependent app {dep_app} version {dep_version}')

def process_request(message):
    try:
        logging.info(f"⭐ Received update request: {message}")
        
        # Diagnostic checks
        logging.info("Checking Docker socket...")
        if os.path.exists('/var/run/docker.sock'):
            socket_perms = os.stat('/var/run/docker.sock')
            logging.info(f"Docker socket exists with permissions: {oct(socket_perms.st_mode)}")
        else:
            logging.error("Docker socket not found!")
            
        import docker
        
        # Try different ways to connect
        try:
            client = docker.from_env()
            logging.info("Successfully created Docker client from env")
        except Exception as e:
            logging.error(f"Failed to create client from env: {e}")
            
        try:
            client = docker.DockerClient(base_url='unix:///var/run/docker.sock')
            logging.info("Successfully created Docker client with unix socket")
        except Exception as e:
            logging.error(f"Failed to create client with unix socket: {e}")

        container_directory = '/app'
        new_version = message.get('new_version')
        docker_compose_file = os.path.join(container_directory, 'docker-compose.yml')
        new_compose_file = os.path.join(container_directory, f'docker-compose-version{new_version.replace(".", "_")}.yml')
        
        # Read existing compose file
        with open(docker_compose_file, 'r') as file:
            compose_data = yaml.safe_load(file)
        
        # Create new compose data with version
        new_compose_data = {
            'version': '3.8',
            'services': compose_data['services'],
            'networks': compose_data['networks']
        }
        
        # Update the image version
        service_name = list(new_compose_data['services'].keys())[0]
        current_image = new_compose_data['services'][service_name]['image']
        repo = current_image.rsplit(':', 1)[0]
        new_image = f"{repo}:{new_version}"
        new_compose_data['services'][service_name]['image'] = new_image
        
        logging.info(f"Attempting to pull image: {new_image}")
        client.images.pull(new_image)
        
        # Write the new compose file
        with open(new_compose_file, 'w') as file:
            yaml.dump(new_compose_data, file, default_flow_style=False, sort_keys=False)

        # Get container configuration from compose file
        container_config = new_compose_data['services'][service_name]
        
        logging.info("Creating new container...")
        # Create and start new container
        container = client.containers.run(
            image=new_image,
            detach=True,
            name=f"{service_name}-{new_version}".replace('.', '_'),
            volumes=container_config.get('volumes', []),
            environment=container_config.get('environment', {}),
            working_dir=container_config.get('working_dir'),
            privileged=container_config.get('privileged', False),
            network=list(new_compose_data['networks'].keys())[0],
            restart_policy={"Name": "unless-stopped"}
        )
        
        logging.info("Waiting for new container to start...")
        time.sleep(5)
        
        # Find and stop old container
        logging.info("Stopping old container...")
        for container in client.containers.list():
            if service_name in container.name and new_version not in container.name:
                container.stop()
                container.remove()
                
        return {
            'success': True,
            'message': f"Successfully updated to version {new_version}"
        }
        
    except Exception as e:
        logging.error(f"Update failed: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }
    
def signal_handler(sig, frame):
    """Handles shutdown signals."""
    logging.info('Shutdown signal received. Exiting...')
    sys.exit(0)

if __name__ == "__main__":
    # Log the loaded environment variables for debugging
    redis_ip = os.getenv('REDIS_HOST')
    logging.info(f"Loaded REDIS_HOST from environment: {redis_ip}")
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Get the directory containing the script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logging.info(f"Script directory: {script_dir}")

    # Load appname and version_number from docker-compose.yml
    appname, version_number = load_docker_compose_data(directory=script_dir)

    # Check Redis host
    if not redis_ip:
        logging.error("REDIS_HOST environment variable is not set.")
        sys.exit(1)

    try:
        # Create connection parameters
        conn_params = ConnectionParameters(
            host=redis_ip,
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 0))
        )

        # Create the Node
        node = Node(
            node_name='docker_rpc_server_machine1',
            connection_params=conn_params
        )

        # Create RPC service with explicit message types
        service = node.create_rpc(
            rpc_name='docker_compose_service_machine1',
            on_request=process_request
        )

        # Start the node in a background thread
        node_thread = threading.Thread(target=node.run, daemon=True)
        node_thread.start()

        # Define dependencies and channel
        channel = 'version_channel'
        dependencies = {
            'app2': '1.1',
            'app3': '1.1'
        }

        # First publish version to establish presence
        publish_version(channel, appname, version_number, redis_ip, dependencies)

        # Set up and start periodic version publishing
        def publish_version_periodically():
            while True:
                try:
                    publish_version(channel, appname, version_number, redis_ip, dependencies)
                    time.sleep(60)
                except Exception as e:
                    logging.error(f"Error publishing version: {e}")
                    time.sleep(5)

        publisher_thread = threading.Thread(target=publish_version_periodically, daemon=True)
        publisher_thread.start()

        while True:
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logging.error(f"Error starting service: {e}")
    finally:
        logging.info("Shutting down services...")
        node.stop()
        sys.exit(0)