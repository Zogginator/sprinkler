#!/bin/bash
cd /home/pi/sprinkler
git pull origin master
sudo systemctl restart sprinkler
journalctl -u sprinkler -f
