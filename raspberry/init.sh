apt-get update -y
apt-get install -y hostapd
# Copy files from the folder to root
cp -r ./root/ /
systemctl unmask hostapd
systemctl enable hostapd
systemctl start hostapd
# Enable the service to node-red
systemctl enable nodered