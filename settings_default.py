from getpass import getpass

fas_user = input('FAS username: ')
fas_pw = getpass('FAS password (will not echo): ')

ipa_instances = ['ipatest01.fedora.idm.elrod.me', 'ipatest02.fedora.idm.elrod.me']
ipa_ssl = input('IPA certificate path: ')
ipa_user = input('IPA username: ')
ipa_pw = getpass('IPA password (will not echo): ')

# * for all
group_search = 'sysadmin-*'

# * for all
user_search = 'codeblock'

skip_groups = False

# After too long a session can expire.
# So we just trigger a re-atuh, even reauth_every account imports.
reauth_every = 150

# We batch our group membership queries.
# How many members maximum should be in each request?
group_chunks = 30
