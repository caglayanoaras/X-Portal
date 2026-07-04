import { BaseGridManager } from './base-grid-manager.js';

const grid = document.querySelector("#user-skills-url");

export class UserSkillManager extends BaseGridManager {
  constructor() {
    super("#user-skills-grid", ["id", "skillname", "skill_level", "notes", "users"]);
  }

  getEntityName() { return "User Skill"; }

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
      addBtn: "user-skills-addSkillBtn",
      saveBtn: "user-skills-saveSkillBtn",
      modal: "user-skills-skillModal",
      modalTitle: "user-skills-skillModalTitle",
      form: "user-skills-skillForm"
    };
  }

  getFormData() {
    return {
      skillname: document.getElementById("user-skills-skillname").value.trim(),
      skill_level: document.getElementById("user-skills-skilllevel").value,
      notes: document.getElementById("user-skills-notes").value.trim()
    };
  }

  validateFormData(formData) {
    if (!formData.skillname) {
      alert("Skill name is required");
      return false;
    }
    return true;
  }

  populateForm(data) {
    document.getElementById("user-skills-skillname").value = data.skillname || "";
    document.getElementById("user-skills-skilllevel").value = data.skill_level || "";
    document.getElementById("user-skills-notes").value = data.notes || "";
  }

  // Alias for backwards compatibility
  loadSkills() { return this.loadData(); }
}