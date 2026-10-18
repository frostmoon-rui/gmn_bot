"""Separate permissions from forms of address: only explicit QQ IDs can receive superadmin permissions."""
import re
from bot_runtime import JsonStore


class AdminRegistry:
    def __init__(self, root, owner):
        self.store = JsonStore(root)
        self.owner = int(owner)
        self.data = self.store.load('admins.json', {'superadmins': {}, 'pending': ['CacheTide']})

    def allowed(self, user):
        return int(user) == self.owner or str(user) in self.data.get('superadmins', {})

    def name(self, user):
        record = self.data.get('superadmins', {}).get(str(user), {})
        return record.get('address', record.get('name', 'admin'))

    def grant(self, actor, user, name):
        if int(actor) != self.owner:
            raise PermissionError('Only the original admin may authorize new superadmins.')
        if not str(user).isdigit() or not 5 <= len(str(user)) <= 12 or not name.strip() or len(name) > 20:
            raise ValueError('Usage: superadmin add:QQ ID|name')
        updated = dict(self.data)
        updated['superadmins'] = dict(self.data.get('superadmins', {}))
        updated['superadmins'][str(user)] = {'name': name.strip(), 'address': name.strip(), 'call_master': False}
        updated['pending'] = [p for p in updated.get('pending', []) if p != name.strip()]
        self.store.save('admins.json', updated)
        self.data = updated

    def phrasing(self, content, user):
        if int(user) != self.owner and str(user) in self.data.get('superadmins', {}):
            return re.sub(r'(?:original admin|admin partner|silly admin|admin)', self.name(user), str(content))
        return content

    def hint(self, user):
        if int(user) != self.owner and self.allowed(user):
            return 'The current user is a superadmin, address them as '+self.name(user)+'; Permissions do not imply a master-servant relationship, Do not call this person master, original admin, admin partner.'
        return ''
