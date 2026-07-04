import { UserManager } from '../grid-managers/user-manager.js';
import { UserRoleManager } from '../grid-managers/user-role-manager.js';
import { UserModuleManager } from '../grid-managers/user-module-manager.js';
import { UserSkillManager } from '../grid-managers/user-skill-manager.js';
import { UsersAndPermissionsSidebarManager } from '../sidebar-managers/users-and-permissions-sidebar-manager.js';

class UsersPermissionsApp {
    constructor() {
        this.managers = {};
        this.initializeManagers();
    }

    initializeManagers() {
        const managerClasses = [
            { name: 'userRoleManager', class: UserRoleManager },
            { name: 'userSkillManager', class: UserSkillManager },
            { name: 'userModuleManager', class: UserModuleManager },
            { name: 'userManager', class: UserManager }
        ];

        managerClasses.forEach(({ name, class: ManagerClass }) => {
            this.managers[name] = new ManagerClass();
        });

        this.sidebarManager = new UsersAndPermissionsSidebarManager(this.managers);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    window.usersPermissionsApp = new UsersPermissionsApp();
});