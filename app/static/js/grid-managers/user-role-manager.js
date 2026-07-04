import { BaseGridManager } from './base-grid-manager.js';

const grid = document.querySelector("#user-roles-url");

export class UserRoleManager extends BaseGridManager {
  constructor() {
    super("#user-roles-grid", ["id", "rolename", "can_create", "can_read", "can_update", "can_delete", "notes", "users"]);
  }

  getEntityName() { return "User Role"; }

  getApiUrls() {
    return {
      getAll: grid.dataset.urlGetAll,
      create: grid.dataset.urlCreate,
      update: grid.dataset.urlUpdate,
      delete: grid.dataset.urlDelete
    };
  }

  getActionButtons() {
    return [
      { action: 'edit', title: 'Edit', icon: 'fas fa-edit', variant: 'primary' },
      { action: 'delete', title: 'Delete', icon: 'fas fa-trash-alt', variant: 'danger' },
      { action: 'show-users', title: 'Show Users', icon: 'fas fa-user', variant: 'info' }
    ];
  }

  getFormElements() {
    return {
      addBtn: "user-roles-addRoleBtn",
      saveBtn: "user-roles-saveRoleBtn",
      modal: "user-roles-roleModal",
      modalTitle: "user-roles-roleModalTitle",
      form: "user-roles-roleForm"
    };
  }

  getFormData() {
    return {
      rolename: document.getElementById("user-roles-roleName").value.trim(),
      notes: document.getElementById("user-roles-notes").value.trim(),
      can_create: document.getElementById("user-roles-canCreate").checked,
      can_read: document.getElementById("user-roles-canRead").checked,
      can_update: document.getElementById("user-roles-canUpdate").checked,
      can_delete: document.getElementById("user-roles-canDelete").checked
    };
  }

  validateFormData(formData) {
    if (!formData.rolename) {
      alert("Role name is required");
      return false;
    }
    return true;
  }

  populateForm(data) {
    document.getElementById("user-roles-roleName").value = data.rolename || "";
    document.getElementById("user-roles-notes").value = data.notes || "";
    document.getElementById("user-roles-canCreate").checked = !!data.can_create;
    document.getElementById("user-roles-canRead").checked = !!data.can_read;
    document.getElementById("user-roles-canUpdate").checked = !!data.can_update;
    document.getElementById("user-roles-canDelete").checked = !!data.can_delete;
  }

  // Alias for backwards compatibility
  loadRoles() { return this.loadData(); }
}