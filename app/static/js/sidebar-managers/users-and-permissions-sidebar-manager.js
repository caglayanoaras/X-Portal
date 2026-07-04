export class UsersAndPermissionsSidebarManager {
    constructor({ userRoleManager, userManager, userSkillManager, userModuleManager }) {
        this.managers = {
            userRoleManager,
            userManager,
            userSkillManager,
            userModuleManager
        };

        // Create the HTML template for sidebar buttons
        this.sidebarButtonsHTMLTemplate = this.createSidebarButtonsHTMLTemplate();

        // Insert the sidebar buttons into the DOM
        this.insertButtonContent();

        // Select all sidebar buttons and content sections
        this.sidebarButtons = document.querySelectorAll('.sidebar-btn');
        this.contentSections = document.querySelectorAll('.content-section');
        this.welcomeContent = document.getElementById('welcome-content');

        // Initialize event listeners for sidebar buttons
        this.initializeEventListeners();
    }

    initializeEventListeners() {
        this.sidebarButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                this.handleSidebarClick(e.currentTarget);
            });
        });
    }

    createSidebarButtonsHTMLTemplate() {
        // Return the HTML template for sidebar buttons
        return `
        <div class="list-group list-group-flush">
            <button type="button" class="list-group-item list-group-item-action d-flex align-items-center py-3 sidebar-btn"
                data-content="users">
                <i class="fas fa-users me-3 text-primary"></i>
                <span>Users</span>
            </button>

            <button type="button" class="list-group-item list-group-item-action d-flex align-items-center py-3 sidebar-btn"
                data-content="user-roles">
                <i class="fas fa-user-shield me-3 text-success"></i>
                <span>User Roles</span>
            </button>

            <button type="button" class="list-group-item list-group-item-action d-flex align-items-center py-3 sidebar-btn"
                data-content="user-skills">
                <i class="fas fa-hammer me-3 text-warning"></i>
                <span>User Skills</span>
            </button>

            <button type="button" class="list-group-item list-group-item-action d-flex align-items-center py-3 sidebar-btn"
                data-content="existing-modules">
                <i class="fas fa-gears me-3 text-secondary"></i>
                <span>Existing Modules</span>
            </button>
        </div>
        `;
    }

    insertButtonContent() {
        // Insert the sidebar buttons HTML into the offcanvas sidebar
        document.querySelector('.offcanvas-body').innerHTML = this.sidebarButtonsHTMLTemplate;
        // Insert the sidebar buttons HTML into the large screen sidebar
        document.querySelector('.sidebar-content').innerHTML = this.sidebarButtonsHTMLTemplate;
    }

    handleSidebarClick(button) {
        const contentType = button.dataset.content;

        // Update active states
        this.sidebarButtons.forEach(btn => btn.classList.remove('active'));
        button.classList.add('active');

        // Hide all content
        this.welcomeContent.style.display = 'none';
        this.contentSections.forEach(section => section.style.display = 'none');

        // Show selected content
        const targetContent = document.getElementById(contentType + '-content');
        if (targetContent) {
            targetContent.style.display = 'block';
            this.handleContentTypeSpecificActions(contentType);
        }
    }

    handleContentTypeSpecificActions(contentType) {
        switch (contentType) {
            case 'user-roles':
                this.managers['userRoleManager']?.loadRoles?.();
                break;
            case 'users':
                this.managers['userManager']?.loadUsers?.();
                break;
            case 'user-skills':
                this.managers['userSkillManager']?.loadSkills?.();
                break;
            case 'existing-modules':
                this.managers['userModuleManager']?.loadModules?.();
                break;
        }
    }
}