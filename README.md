# Set default Google DNS to bypass pihole:
# Edit /etc/docker/daemon.json and add the following:
{ "dns" : [ "8.8.8.8", "8.8.4.4" ] }

# You need to install qemu on host system to run x86 binaries on aarch64 (raspberry pi):
sudo apt install -y binfmt-support qemu-kvm qemu-user-static

# build image:
docker build -t security_camera .

# To run using docker compose:
docker compose up -d

