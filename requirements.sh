if grep -q '"platform":.*"mycroft_mark_.*"' /etc/mycroft/mycroft.conf; then
    sudo apt-get install --force-yes -yq libgeos-dev -o DPkg::Options::=--force-confdef
fi