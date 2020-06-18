# .bashrc

alias fas2ipa-resetdata="sudo ipa-restore /var/lib/ipa/backup/`sudo ls -1rt /var/lib/ipa/backup | tail -1` -p adminPassw0rd!"
alias fas2ipa-run="pushd /vagrant/; poetry run fas2ipa; popd;"

cd /vagrant
