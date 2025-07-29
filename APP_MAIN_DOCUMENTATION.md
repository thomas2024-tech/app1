# App1 Main Application Documentation

## Overview

The `main.py` file in `firmwares/app1` implements a containerized application that serves dual purposes: it acts as both a **version reporter** and an **RPC service** for Docker container management. This application is designed to work in a distributed system where multiple similar applications run on different machines and communicate through Redis messaging.

## System Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        App1 Container                           │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │   Version Reporter  │    │    RPC Service                  │ │
│  │                     │    │                                 │ │
│  │ - Reads own version │    │ - Receives update commands      │ │
│  │ - Publishes to      │    │ - Updates docker-compose.yml   │ │
│  │   Redis channel     │    │ - Restarts containers          │ │
│  └─────────┬───────────┘    └─────────────┬───────────────────┘ │
└────────────┼────────────────────────────────┼───────────────────┘
             │                                │
             │ Publishes                      │ Receives RPC
             │ VersionMessage                 │ Commands
             ▼                                ▼
    ┌────────────────┐              ┌─────────────────┐
    │     Redis      │              │    Updater      │
    │   Pub/Sub      │◄─────────────┤    Service      │
    │   Channel      │              │  (Remote)       │
    └────────────────┘              └─────────────────┘
```

### Core Components

1. **Version Reporter**: Publishes application version information
2. **RPC Service**: Handles remote Docker container management commands
3. **Docker Compose Manager**: Updates and manages container configurations
4. **Signal Handler**: Graceful shutdown management

## Detailed Component Analysis

### 1. Message Classes

#### VersionMessage
```python
class VersionMessage(PubSubMessage):
    appname: str
    version_number: str
    dependencies: dict
```

**Purpose**: Represents version information published to Redis pub-sub channel.

**Attributes**:
- `appname`: Unique identifier for this application (extracted from Docker image)
- `version_number`: Current version tag (extracted from Docker image)
- `dependencies`: Dictionary of dependent applications (empty in current implementation)

#### DockerCommandRequest
```python
class DockerCommandRequest(RPCMessage):
    command: str
    directory: str
    new_version: str = None
```

**Purpose**: RPC message format for receiving Docker management commands.

**Commands Supported**:
- `down`: Stops the Docker containers
- `update_version`: Updates container to new version and restarts

#### DockerCommandResponse
```python
class DockerCommandResponse(RPCMessage):
    success: bool
    message: str
```

**Purpose**: RPC response indicating command execution results.

### 2. Docker Compose Management

#### `load_docker_compose_data(directory='.', filename='docker-compose.yml')`

**Purpose**: Extracts application name and version from Docker Compose configuration.

**Process**:
1. Reads `docker-compose.yml` from specified directory
2. Extracts the first service definition
3. Parses the image string format: `repository/appname:version`
4. Returns `(appname, version_number)` tuple

**Example**:
```yaml
# docker-compose.yml
services:
  app1:
    image: "myrepo/app1:2.1.0"
```
Returns: `("app1", "2.1.0")`

**Error Handling**:
- File not found: Logs error and exits
- Invalid YAML: Logs error and exits
- Invalid image format: Logs error and exits

### 3. Version Publishing

#### `publish_version(channel, appname, version_number, redis_ip, dependencies=None)`

**Purpose**: Publishes current version information to Redis pub-sub channel.

**Parameters**:
- `channel`: Redis channel name (typically 'version_channel')
- `appname`: Application identifier
- `version_number`: Current version
- `redis_ip`: Redis server hostname/IP
- `dependencies`: Dict of required app versions (empty in current implementation)

**Process**:
1. Creates Redis connection using environment variables
2. Initializes Publisher with VersionMessage type
3. Publishes message to specified channel
4. Logs publication confirmation

**Environment Variables Used**:
- `REDIS_HOST`: Redis server hostname
- `REDIS_PORT`: Redis server port (default: 6379)
- `REDIS_DB`: Redis database number (default: 0)

### 4. RPC Service Implementation

#### `DockerComposeRPCService`

**Purpose**: Handles incoming RPC requests for Docker container management.

**Key Methods**:

##### `__init__(node: Node, rpc_name: str)`
- Initializes the RPC service with node and service name
- Sets message types for request/response handling
- Service name format: `docker_compose_service_machine1`

##### `handle_message(message: DockerCommandRequest) -> DockerCommandResponse`
**Core business logic** for processing RPC commands.

**Command Processing**:

**1. `down` Command**:
```python
docker-compose -f {docker_compose_file} down
```
- Stops all containers defined in docker-compose.yml
- Returns success/failure status with output

**2. `update_version` Command**:
```python
# Process:
1. Read current docker-compose.yml
2. Parse image string: repo/app:old_version
3. Update to: repo/app:new_version
4. Write updated docker-compose.yml
5. docker-compose down
6. docker-compose up -d
```

**Update Process Details**:
1. **Read Configuration**: Loads current docker-compose.yml
2. **Parse Image**: Extracts repo, appname, and current version
3. **Update Image**: Constructs new image string with target version
4. **Write Configuration**: Saves updated docker-compose.yml
5. **Stop Containers**: Executes `docker-compose down`
6. **Start Containers**: Executes `docker-compose up -d`

**Error Handling**:
- Subprocess errors: Captures stderr and returns failure response
- File I/O errors: Returns exception details in response
- Unknown commands: Returns "Unknown command" error

### 5. Main Application Flow

#### Initialization Sequence

```python
if __name__ == "__main__":
    # 1. Environment Setup
    redis_ip = os.getenv('REDIS_HOST')
    signal.signal(SIGINT, signal_handler)
    signal.signal(SIGTERM, signal_handler)
    
    # 2. Version Discovery
    appname, version_number = load_docker_compose_data()
    
    # 3. Redis Connection
    conn_params = ConnectionParameters(...)
    
    # 4. Node Initialization
    node = Node(node_name='docker_rpc_server_machine1', ...)
    
    # 5. RPC Service Setup
    service = DockerComposeRPCService(...)
    
    # 6. Version Publication
    publish_version(channel, appname, version_number, redis_ip, {})
    
    # 7. Service Execution
    service.run()  # Blocks indefinitely
```

#### Configuration Details

**Node Configuration**:
- **Node Name**: `docker_rpc_server_machine1`
- **RPC Service Name**: `docker_compose_service_machine1`
- **Purpose**: Unique identifiers for RPC communication routing

**Version Publishing**:
- **Channel**: `version_channel`
- **Dependencies**: `{}` (empty - no dependency management)
- **Frequency**: Once on startup

#### Threading Model

**Main Thread**: Runs RPC service message processing loop
**Background Thread**: Node communication handling (daemon thread)

**Thread Safety**: 
- Node runs in separate daemon thread
- RPC service processes messages sequentially in main thread
- No shared mutable state between threads

## Message Formats

### Version Publication Message
```json
{
  "appname": "app1",
  "version_number": "2.1.0",
  "dependencies": {}
}
```

### RPC Request Messages
```json
{
  "command": "update_version",
  "directory": "/app",
  "new_version": "2.2.0"
}
```

```json
{
  "command": "down",
  "directory": "/app",
  "new_version": null
}
```

### RPC Response Messages
```json
{
  "success": true,
  "message": "Updated app1 to version 2.2.0"
}
```

```json
{
  "success": false,
  "message": "Error: container not found"
}
```

## Configuration Management

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_HOST` | Yes | - | Redis server hostname |
| `REDIS_PORT` | No | 6379 | Redis server port |
| `REDIS_DB` | No | 0 | Redis database number |

### Docker Compose Requirements

**File Location**: Must be in same directory as `main.py`
**File Name**: `docker-compose.yml`

**Required Structure**:
```yaml
services:
  service_name:
    image: "repository/appname:version"
    # ... other configuration
```

**Image Format**: `repository/appname:version`
- **repository**: Docker registry repository
- **appname**: Application identifier (must match expected naming)
- **version**: Semantic version tag

### Example Docker Compose Configuration
```yaml
version: '3.8'
services:
  app1:
    image: "myrepo/app1:2.1.0"
    container_name: app1-container
    ports:
      - "8080:8080"
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_DB=0
    restart: unless-stopped
```

## Integration with Updater System

### Communication Flow

**1. Version Reporting**:
```
app1 startup → Read docker-compose.yml → Extract version → Publish to Redis
```

**2. Update Request**:
```
Updater → RPC Call → app1 → Update docker-compose.yml → Restart containers
```

**3. New Version Reporting**:
```
app1 restart → Read updated docker-compose.yml → Publish new version → Redis
```

### RPC Service Registration

**Service Mapping in Updater**:
```python
service_mapping = {
    'app1': 'docker_compose_service_machine1',
    'app2': 'docker_compose_service_machine2', 
    'app3': 'docker_compose_service_machine3'
}
```

**Node Names**:
- **app1**: `docker_rpc_server_machine1`
- **Service**: `docker_compose_service_machine1`

### Update Process Integration

**Complete Update Flow**:
1. **app1** publishes current version to Redis
2. **Updater** receives version message
3. **Updater** checks against `APPS_TO_UPDATE` configuration
4. **Updater** sends RPC `update_version` command to app1
5. **app1** updates docker-compose.yml and restarts containers
6. **app1** publishes new version to Redis
7. **Updater** confirms successful update

## Error Handling and Logging

### Logging Strategy

**Log Level**: INFO
**Format**: `%(asctime)s - %(levelname)s - %(message)s`
**Output**: Standard output (captured by Docker)

### Error Categories

**1. Configuration Errors**:
- Missing docker-compose.yml file
- Invalid image format
- Missing environment variables

**2. Runtime Errors**:
- Redis connection failures
- Docker command execution failures
- RPC message processing errors

**3. Recovery Strategies**:
- Configuration errors: Exit application (restart required)
- Runtime errors: Log error and continue operation
- Docker command failures: Return error in RPC response

### Example Log Output

**Normal Operation**:
```
2024-01-15 10:30:01 - INFO - Looking for docker-compose file at: /app/docker-compose.yml
2024-01-15 10:30:01 - INFO - Script directory: /app
2024-01-15 10:30:01 - INFO - Loaded REDIS_HOST from environment: redis
2024-01-15 10:30:02 - INFO - Published version 2.1.0 of app app1 to channel version_channel
```

**Update Operation**:
```
2024-01-15 10:35:01 - INFO - Received RPC command: update_version to 2.2.0
2024-01-15 10:35:02 - INFO - Updated docker-compose.yml with new version 2.2.0
2024-01-15 10:35:05 - INFO - Containers restarted successfully
```

## Deployment Considerations

### Docker Container Requirements

**Base Image**: Python 3.9+ with Docker CLI
**Required Packages**:
- PyYAML (docker-compose.yml parsing)
- commlib-py (Redis communication)
- python-dotenv (environment management)

**Volume Mounts**:
- Docker socket: `/var/run/docker.sock:/var/run/docker.sock`
- Application directory: `/app`
- Docker Compose file: `/app/docker-compose.yml`

### Network Configuration

**Redis Connectivity**:
- Must have network access to Redis server
- Default port: 6379
- Database: 0 (configurable)

**RPC Communication**:
- Node name must be unique across the system
- Service name must match updater configuration

### Security Considerations

**Docker Socket Access**:
- Container requires Docker socket access for container management
- Security implication: Container can manage other containers
- Mitigation: Run in trusted environment with appropriate permissions

**Network Security**:
- Redis communication is unencrypted
- RPC messages contain sensitive update commands
- Recommendation: Use VPN or private networks





