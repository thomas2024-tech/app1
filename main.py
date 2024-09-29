from commlib.transports.redis import ConnectionParameters, Publisher
from commlib.pubsub import PubSubMessage
import os

#This is the firmware of first app

class VersionMessage(PubSubMessage):
    """Message format for version updates."""
    appname: str
    version_number: str

def publish_version(channel, appname, version_number):
    # Define connection parameters for Redis
    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    redis_db = int(os.getenv('REDIS_DB', 0))

    conn_params = ConnectionParameters(
        host=redis_host,
        port=redis_port,
        db=redis_db
    )

    # Initialize the Publisher with the specific channel/topic
    publisher = Publisher(
        conn_params=conn_params,
        topic=channel,
        msg_type=VersionMessage
    )

    # Create a VersionMessage
    message = VersionMessage(appname=appname, version_number=version_number)

    # Publish the message
    publisher.publish(message)

    print(f'Published version {version_number} of app {appname} to channel {channel}')

if __name__ == "__main__":
    # Example usage
    publish_version('version_channel', 'app1', '1.3')
