# fas2ipa

Assumptions:

* Account with admin privileges on the IPA server
* Account with privileges enough to dump users and groups in FAS
* `python-fedora`, `python-requests`, `python_freeipa`, `progressbar2`

## Development environment
Vagrant allows contributors to get quickly up and running with a development environment
by automatically configuring a virtual machine running FreeIPA. To get started, first install
the Vagrant and Virtualization packages needed, and start the libvirt service:


```
$ sudo dnf install ansible libvirt vagrant-libvirt vagrant-sshfs vagrant-hostmanager
$ sudo systemctl enable libvirtd
$ sudo systemctl start libvirtd
```

Check out the code and run vagrant up:

```
$ git clone https://github.com/fedora-infra/fas2ipa
$ cd fas2ipa
$ vagrant up
```

Your newly installed IPA Server will be viewable on your host machine at http://ipa.fas2ipa.test


Next, SSH into your newly provisioned development environment:

```
$ vagrant ssh
```

After initial setup, you will need to add FAS credentials to `/vagrant/config.toml`
by replacing the following two lines in that file:

```
[fas]
# username =
# password =
```

The vagrant machine has a handful of aliases configured to help development.

`fas2ipa-resetdata` restores the freeIPA data to a backup that was taken
during the provisioning of the vagrant machine

`fas2ipa-run` runs the tool itself