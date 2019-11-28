from getpass import getpass

fas_user = input('FAS username: ')
fas_pw = getpass('FAS password (will not echo): ')

ipa_instance = input('IPA hostname: ')
ipa_ssl = input('IPA certificate path: ')
ipa_user = input('IPA username: ')
ipa_pw = getpass('IPA password (will not echo): ')

# * for all
group_search = 'sysadmin-*'

# * for all
user_search = 'codeblock'

skip_groups = False
