import { BaseGridManager } from './base-grid-manager.js';

const grid = document.querySelector("#users-url");

export class UserManager extends BaseGridManager {
  constructor() {
    super("#users-grid", ["id", "is_active", "username", "name", "surname", "email", "usertype", "title", "roles", "skills", "divisions", "modules"]);
    this.tomSelectInstances = {
      roles: null,
      skills: null,
      modules: null
    };
  }

  async initializeTomSelects() {
    try {
      this.destroyTomSelects();

      const [rolesData, skillsData, modulesData] = await Promise.all([
        this.fetchUserRoles(),
        this.fetchUserSkills(),
        this.fetchExistingModules()
      ]);

      this.tomSelectInstances.roles = new TomSelect('#users-roles', {
        plugins: ['remove_button'],
        valueField: 'id',
        labelField: 'rolename',
        searchField: 'rolename',
        options: rolesData,
        create: false,
        placeholder: 'Select roles...'
      });

      this.tomSelectInstances.skills = new TomSelect('#users-skills', {
        plugins: ['remove_button'],
        valueField: 'id',
        labelField: 'skillname',
        searchField: 'skillname',
        options: skillsData,
        create: false,
        placeholder: 'Select skills...'
      });


      this.tomSelectInstances.modules = new TomSelect('#users-modules', {
        plugins: ['remove_button'],
        valueField: 'id',
        labelField: 'title',
        searchField: 'title',
        options: modulesData,
        create: false,
        placeholder: 'Select modules...'
      });

    } catch (error) {
      console.error("Error initializing Tom Select:", error);
    }
  }

  getEntityName() { return "User"; }

  getUpdateIdentifier(data) {
    return data.username;
  }

  getApiUrls() {
    return {
      getAll: grid.dataset.urlGetAll,
      create: grid.dataset.urlCreate,
      update: grid.dataset.urlUpdate,
      delete: grid.dataset.urlDelete
    };
  }

  getFormElements() {
    return {
      addBtn: "users-addUserBtn",
      saveBtn: "users-saveUserBtn",
      modal: "users-Modal",
      modalTitle: "users-ModalTitle",
      form: "users-Form"
    };
  }

  getFormData() {
    const formData = {
      username: document.getElementById("users-username").value.trim(),
      email: document.getElementById("users-email").value.trim(),
      name: document.getElementById("users-name").value.trim(),
      surname: document.getElementById("users-surname").value.trim(),
      usertype: document.getElementById("users-usertype").value,
      is_active: document.getElementById("users-isActive").checked,
      title: document.getElementById("users-title").value.trim()
    };

    const password = document.getElementById("users-password").value;
    if (password) {
      formData.pw = password;
    }

    if (this.tomSelectInstances.roles) {
      formData.roles = this.tomSelectInstances.roles.getValue().map(id => parseInt(id));
    }
    if (this.tomSelectInstances.skills) {
      formData.skills = this.tomSelectInstances.skills.getValue().map(id => parseInt(id));
    }
    if (this.tomSelectInstances.modules) {
      formData.modules = this.tomSelectInstances.modules.getValue().map(id => parseInt(id));
    }
    return formData;
  }

  getCustomColumnDef(key) {
    if (key === "roles") {
      return {
        field: "roles",
        headerName: "ROLES",
        valueFormatter: (params) => `${params.data.roles?.length || 0} role(s)`,
        comparator: (a, b, nodeA, nodeB) => {
          const lenA = nodeA.data.roles?.length || 0
          const lenB = nodeB.data.roles?.length || 0
          return lenA - lenB;
        },
        filter: false
      };
    }

    if (key === "skills") {
      return {
        field: "skills",
        headerName: "SKILLS",
        valueFormatter: (params) => `${params.data.skills?.length || 0} skill(s)`,
        comparator: (a, b, nodeA, nodeB) => {
          const lenA = nodeA.data.skills?.length || 0
          const lenB = nodeB.data.skills?.length || 0
          return lenA - lenB;
        },
        filter: false
      };
    }

    if (key === "modules") {
      return {
        field: "modules",
        headerName: "MODULES",
        valueFormatter: (params) => `${params.data.modules?.length || 0} module(s)`,
        comparator: (a, b, nodeA, nodeB) => {
          const lenA = nodeA.data.modules?.length || 0
          const lenB = nodeB.data.modules?.length || 0
          return lenA - lenB;
        },
        filter: false
      };
    }

    return null;
  }

  getActionButtons() {
    return [
      { action: 'edit', title: 'Edit', icon: 'fas fa-edit', variant: 'primary' },
      { action: 'delete', title: 'Delete', icon: 'fas fa-trash-alt', variant: 'danger' },
      { action: 'show-roles', title: 'Show Roles', icon: 'fas fa-user-shield', variant: 'success' },
      { action: 'show-skills', title: 'Show Skills', icon: 'fas fa-hammer', variant: 'warning' },
      { action: 'show-modules', title: 'Show Modules', icon: 'fas fa-gears', variant: 'secondary' }
    ];
  }

  getCustomActionHandler(action) {
    const handlers = {
      'show-roles': (params) => this.showUserRoles(params.data.roles || []),
      'show-skills': (params) => this.showUserSkills(params.data.skills || []),
      'show-modules': (params) => this.showUserModules(params.data.modules || [])
    };
    return handlers[action];
  }

  showUserRoles(roles) {
    this.showItemInModal(roles, "Roles");
  }

  showUserSkills(skills) {
    this.showItemInModal(skills, "Skills");
  }

  showUserModules(modules) {
    this.showItemInModal(modules, "Modules");
  }

  validateFormData(formData, isEdit = false) {
    if (!formData.username) {
      alert("Username is required");
      return false;
    }
    if (!formData.email) {
      alert("Email is required");
      return false;
    }
    if (!formData.name) {
      alert("First name is required");
      return false;
    }
    if (!formData.surname) {
      alert("Last name is required");
      return false;
    }
    if (!isEdit && !formData.pw) {
      alert("Password is required for new users");
      return false;
    }
    return true;
  }

  populateForm(data) {
    document.getElementById("users-username").value = data.username || "";
    document.getElementById("users-email").value = data.email || "";
    document.getElementById("users-name").value = data.name || "";
    document.getElementById("users-surname").value = data.surname || "";
    document.getElementById("users-usertype").value = data.usertype || "user";
    document.getElementById("users-isActive").checked = !!data.is_active;
    document.getElementById("users-title").value = data.title || "";
    document.getElementById("users-password").value = "";

    this.populateTomSelects(data);
  }

  populateTomSelects(userData) {
    if (userData.roles && this.tomSelectInstances.roles) {
      const roleIds = userData.roles.map(role => role.id.toString());
      this.tomSelectInstances.roles.setValue(roleIds);
    }

    if (userData.skills && this.tomSelectInstances.skills) {
      const skillIds = userData.skills.map(skill => skill.id.toString());
      this.tomSelectInstances.skills.setValue(skillIds);
    }

    if (userData.modules && this.tomSelectInstances.modules) {
      const moduleIds = userData.modules.map(module => module.id.toString());
      this.tomSelectInstances.modules.setValue(moduleIds);
    }

    if (userData.divisions && this.tomSelectInstances.divisions) {
      const divisionCodes = userData.divisions.map(division => division.code);
      this.tomSelectInstances.divisions.setValue(divisionCodes);
    }
  }

  // Alias for backwards compatibility
  loadUsers() { return this.loadData(); }
}