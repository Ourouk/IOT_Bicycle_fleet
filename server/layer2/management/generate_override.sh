#!/bin/bash

# File to generate
override_file="docker-compose.override.yml"

# List of services to apply extra_hosts to
services=("caddy")

# List of specific hostnames to extract from /etc/hosts
filter_hosts=("hepl.local")

# Function to generate the extra_hosts block
generate_extra_hosts() {
    echo "    extra_hosts:"
    # Extract IP and hostname pairs from /etc/hosts using sed
    sed -nE 's/^([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)[[:space:]]+([^[:space:]]+).*/\1 \2/p' /etc/hosts | while read -r ip host; do
        for filter in "${filter_hosts[@]}"; do
            if [ "$host" = "$filter" ]; then
                echo "      - \"$host:$ip\""
            fi
        done
    done
}

# Generate docker-compose.override.yml with dynamic extra_hosts
{
    echo "services:"
    for service in "${services[@]}"; do
        echo "  $service:"
        generate_extra_hosts
    done
} > "$override_file"

# Start the containers with the new override file
docker compose up -d
