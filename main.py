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
from commlib.rpc import BaseRPCService, RPCMessage
import time
import threading

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# VersionMessage class
class VersionMessage(PubSubMessage):
    appname: str
    version_number: str
    dependencies: dict

# RPC message classes
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

class DockerComposeRPCService(BaseRPCService):
    def __init__(self, node: Node, rpc_name: str):
        # Initialize the base service first with the rpc_name
        super().__init__(
            msg_type=DockerCommandRequest,
            rpc_name=rpc_name
        )
        # Store the node reference and set up message types
        self._node = node
        self.msg_type = DockerCommandRequest
        self.resp_type = DockerCommandResponse
        # Register this service with the node
        node.add_rpc_service(self)
    
    def process_request(self, message: DockerCommandRequest) -> DockerCommandResponse:
        """
        Process incoming Docker command requests.
        This is the method that BaseRPCService expects us to implement.
        """
        logging.info(f"Processing request: {message.command} for directory: {message.directory}")
        
        command = message.command
        directory = message.directory
        docker_compose_file = os.path.join(directory, 'docker-compose.yml')
        
        if command == 'down':
            try:
                result = subprocess.run(
                    ["docker-compose", "-f", docker_compose_file, "down"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return DockerCommandResponse(success=True, message=f"'docker-compose down' succeeded in {directory}")
                else:
                    return DockerCommandResponse(success=False, message=f"Error: {result.stderr}")
            except Exception as e:
                return DockerCommandResponse(success=False, message=f"Exception occurred: {e}")
        
        elif command == 'update_version':
            new_version = message.new_version
            try:
                with open(docker_compose_file, 'r') as file:
                    compose_data = yaml.safe_load(file)
                service_name = list(compose_data['services'].keys())[0]
                image = compose_data['services'][service_name]['image']
                repo, appname_with_version = image.split('/')
                appname, current_version = appname_with_version.split(':')
                new_image = f"{repo}/{appname}:{new_version}"
                compose_data['services'][service_name]['image'] = new_image
                
                with open(docker_compose_file, 'w') as file:
                    yaml.dump(compose_data, file)
                
                subprocess.run(
                    ["docker-compose", "-f", docker_compose_file, "down"],
                    check=True
                )
                subprocess.run(
                    ["docker-compose", "-f", docker_compose_file, "up", "-d"],
                    check=True
                )
                return DockerCommandResponse(success=True, message=f"Updated {appname} to version {new_version}")
            except subprocess.CalledProcessError as e:
                return DockerCommandResponse(success=False, message=f"Subprocess error: {e}")
            except Exception as e:
                return DockerCommandResponse(success=False, message=f"Exception occurred: {e}")
        
        else:
            return DockerCommandResponse(success=False, message=f"Unknown command '{command}'")

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

        # Initialize the Node
        node = Node(
            node_name='docker_rpc_server_machine1',
            connection_params=conn_params
        )

        # Create the RPC service before starting the node
        service = DockerComposeRPCService(
            node=node,
            rpc_name='docker_compose_service_machine1'
        )

        # Start the node in a background thread
        node_thread = threading.Thread(target=node.run, daemon=True)
        node_thread.start()

        # Set up periodic version publishing
        def publish_version_periodically():
            while True:
                try:
                    publish_version(channel, appname, version_number, redis_ip, dependencies)
                    time.sleep(60)
                except Exception as e:
                    logging.error(f"Error publishing version: {e}")
                    time.sleep(5)

        # Publish initial version information
        channel = 'version_channel'
        dependencies = {
            'app2': '1.1',
            'app3': '1.1'
        }

        # First publish version to establish presence
        publish_version(channel, appname, version_number, redis_ip, dependencies)

        # Start version publisher in background
        publisher_thread = threading.Thread(target=publish_version_periodically, daemon=True)
        publisher_thread.start()

        # Keep the main thread alive while monitoring threads
        try:
            while True:
                if not node_thread.is_alive():
                    raise Exception("Node thread died unexpectedly")
                if not publisher_thread.is_alive():
                    raise Exception("Publisher thread died unexpectedly")
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
        finally:
            # Attempt graceful shutdown
            logging.info("Shutting down services...")
            node.stop()
            sys.exit(0)

    except Exception as e:
        logging.error(f"Error starting service: {e}")
        sys.exit(1)