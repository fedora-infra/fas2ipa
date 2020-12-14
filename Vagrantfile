# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.configure(2) do |config|
  config.vm.box_url = "https://download.fedoraproject.org/pub/fedora/linux/releases/33/Cloud/x86_64/images/Fedora-Cloud-Base-Vagrant-33-1.2.x86_64.vagrant-libvirt.box"
  config.vm.box = "f33-cloud-libvirt"
  config.vm.hostname = "ipa.fas2ipa.test"
  config.vm.synced_folder ".", "/vagrant", type: "sshfs"
  config.hostmanager.enabled = true
  config.hostmanager.manage_host = true
  config.vm.provider :libvirt do |libvirt|
    libvirt.cpus = 2
    libvirt.memory = 4096
  end

  # Vagrant adds '127.0.0.1 ipa.example.com ipa' as the first line in /etc/hosts
  # and freeipa doesnt like that, so we remove it
  config.vm.provision "shell", inline: "sudo sed -i '1d' /etc/hosts"

  config.vm.provision "ansible" do |ansible|
    ansible.playbook = "devel/ansible/playbook.yml"
    ansible.config_file = "devel/ansible/ansible.cfg"
  end

end
