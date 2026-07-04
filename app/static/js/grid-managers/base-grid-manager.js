// Base class for all grid managers
export class BaseGridManager {
    constructor(gridSelector, preferredColumnOrder = []) {
        this.gridDiv = document.querySelector(gridSelector);
        this.preferredColumnOrder = preferredColumnOrder;
        this.currentMode = null; // "add" | "edit"
        this.modal = null;
        this.tomSelectInstances = {};

        this.initializeEventListeners();
    }

    // Abstract methods to be implemented by subclasses
    getEntityName() { throw new Error("Must implement getEntityName()"); }
    getApiUrls() { throw new Error("Must implement getApiUrls()"); }
    getFormElements() { throw new Error("Must implement getFormElements()"); } // addBtn saveBtn keys should exist inside the FormElements
    getFormData() { throw new Error("Must implement getFormData()"); }

    getCustomColumnDef(key) { } // Override in subclasses for custom column definitions
    getActionButtons() { } // Override in subclasses to define action buttons
    getCustomActionHandler(action) { }
    validateFormData(formData, isEdit = false) { } // Validation is not needed because backend will handle it.
    async initializeTomSelects() {}
    populateForm(data) { }
    updateFormVisibility() { }
    getUpdateIdentifier(data) { return data.id } // Override in subclasses to define how to identify the item for updates

    // Common initialization
    initializeEventListeners() {
        const elements = this.getFormElements();

        // Add button
        document.getElementById(elements.addBtn)?.addEventListener("click", () => {
            this.showModal("add");
        });

        // Save button
        document.getElementById(elements.saveBtn)?.addEventListener("click", () => {
            if (this.currentMode === "add") {
                this.handleSaveNew();
            } else if (this.currentMode === "edit") {
                this.handleSaveEdit();
            }
        });

        // Delete confirmation
        document.getElementById("confirmDeleteBtn")?.addEventListener("click", () => {
            this.handleConfirmDelete();
        });
    }

    // Common load method
    async loadData() {
        try {
            const data = await this.fetchData(this.getApiUrls().getAll);
            const gridOptions = this.createGridOptions(data);
            this.initializeGrid(gridOptions);
        } catch (error) {
            this.handleError(`Error loading ${this.getEntityName()}s`, error);
        }
    }

    // Common fetch method
    async fetchData(data_url_id) {
        const response = await fetch(data_url_id, {
            method: 'GET',
            headers: { 'Accept': 'application/json' }
        });

        if (!response.ok) {
            throw new Error(`Failed to fetch ${data_url_id}: ${response.status}`);
        }

        return await response.json();
    }

    // Common grid options creation
    createGridOptions(data) {
        const hasData = Array.isArray(data) && data.length > 0;
        return {
            rowData: hasData ? data : [],
            columnDefs: hasData ? this.createColumnDefs(data[0]) : [],
            sideBar: {
                toolPanels: ['columns', 'filters-new'],
                defaultToolPanel: null,
            },
            enableFilterHandlers: true,
            suppressSetFilterByDefault: true,
            defaultColDef: {
                resizable: true,
                sortable: true,
                flex: 1,
                minWidth: 100,
                filter: "agSelectableColumnFilter",
                suppressHeaderFilterButton: true,
                // enableRowGroup: true,
            },
            getRowId: (params) => params.data.id,
            onCellClicked: this.handleCellClick.bind(this)
        };
    }

    // Common column definitions creation
    createColumnDefs(firstRow) {
        const allKeys = Object.keys(firstRow);
        const orderedKeys = [
            ...this.preferredColumnOrder,
            ...allKeys.filter(key => !this.preferredColumnOrder.includes(key))
        ];
        const columnDefs = orderedKeys.map(key => this.createColumnDef(key));
        columnDefs.push(this.createActionsColumn());

        return columnDefs;
    }

    // Common column definition creation
    createColumnDef(key) {
        // Special handling for common field types

        if (key === "id") {
            return {
                field: "id",
                headerName: "ID",
                maxWidth: 95,
            };
        }

        if (key === "code") {
            return {
                field: "code",
                headerName: "CODE",
                maxWidth: 110,
            };
        }

        if (key === "description") {
            return {
                field: key,
                headerName: key.replace(/_/g, " ").toUpperCase(),
                hide: true
            };
        }

        if (key === "users") {
            return {
                field: "users",
                headerName: "USERS",
                valueFormatter: (params) => `${params.data.users?.length || 0} user(s)`,
                comparator: (a, b, nodeA, nodeB) => {
                    const lenA = nodeA.data.users?.length || 0;
                    const lenB = nodeB.data.users?.length || 0;
                    return lenA - lenB;
                },
                filter: false
            };
        }

        if (key === "materials") {
            return {
                field: "materials",
                headerName: "MATERIALS",
                valueGetter: (params) => params.data.materials?.length || 0,
            };
        }

        if (key === "is_active") {
            return {
                field: "is_active",
                headerName: "STATUS",
                cellRenderer: (params) => {
                    const isActive = params.value;
                    const statusClass = isActive ? "text-success" : "text-danger";
                    const statusText = isActive ? "Active" : "Inactive";
                    const icon = isActive ? "fas fa-check-circle" : "fas fa-times-circle";
                    return `<span class="${statusClass}"><i class="${icon} me-1"></i>${statusText}</span>`;
                },
                filter: false,
                minWidth: 100
            };
        }

        if (key === "created_at") {
            return {
                field: key,
                headerName: key.replace(/_/g, " ").toUpperCase(),
                hide: true
            };
        }
        if (key === "created_by") {
            return {
                field: key,
                headerName: key.replace(/_/g, " ").toUpperCase(),
                hide: true
            };
        }
        if (key === "last_modified_at") {
            return {
                field: key,
                headerName: key.replace(/_/g, " ").toUpperCase(),
                hide: true
            };
        }
        if (key === "last_modified_by") {
            return {
                field: key,
                headerName: key.replace(/_/g, " ").toUpperCase(),
                hide: true
            };
        }

        // Allow subclasses to override column definitions
        const customColumnDef = this.getCustomColumnDef(key);
        if (customColumnDef) {
            return customColumnDef;
        }

        // Default column definition
        return {
            field: key,
            headerName: key.replace(/_/g, " ").toUpperCase(),
            filter: "agSelectableColumnFilter",
        };
    }

    // Common actions column creation
    createActionsColumn() {
        const actions = this.getActionButtons();
        return {
            headerName: 'ACTIONS',
            field: 'actions',
            filter: false,
            minWidth: actions.length * 50,
            cellRenderer: () => `
                <div class="btn-group" role="group">
                    ${actions.map(action => `
                        <button class="btn btn-sm btn-outline-${action.variant}"
                            title="${action.title}" data-action="${action.action}">
                            <i class="${action.icon}"></i>
                        </button>
                    `).join('')}
                </div>
            `
        };
    }

    // Common cell click handler
    handleCellClick(params) {
        const button = params.event.target.closest("button");
        if (!button || !button.dataset.action) return;

        const action = button.dataset.action;
        this.handleAction(action, params);
    }

    // Override in subclasses for custom action handling
    handleAction(action, params) {
        const commonActions = {
            'show-users': () => this.showUsers(params.data.users || []),
            'edit': () => this.showModal("edit", params),
            'delete': () => this.showDeleteConfirmation(params)
        };

        const handler = commonActions[action] || this.getCustomActionHandler(action);
        if (handler) {
            handler(params);
        }
    }

    showUsers(users) {
        this.showItemInModal(users, 'Users');
    }

    // Linked objects are shown as lists in a modal.
    showItemInModal(items, type) {
        const linksList = document.getElementById("linksList");
        const modalTitle = document.querySelector("#showLinksModal .modal-title");

        modalTitle.textContent = `${type} (${items.length})`;
        linksList.innerHTML = "";

        if (items.length === 0) {
            linksList.innerHTML = `<li class="list-group-item text-muted">No ${type} assigned</li>`;
        } else {
            items.forEach(item => {
                const listItem = this.createItemListItem(item, type);
                linksList.appendChild(listItem);
            });
        }

        const modal = bootstrap.Modal.getOrCreateInstance(
            document.getElementById("showLinksModal")
        );
        modal.show();
    }

    createItemListItem(item, type) {
        const li = document.createElement("li");
        li.className = "list-group-item";

        let content = '';
        if (type === 'Roles') {
            content = `
                <strong>${item.rolename}</strong>
                <div><small>${item.notes || 'No notes'}</small></div>
            `;
        } else if (type === 'Skills') {
            content = `
                <strong>${item.skillname}</strong>
                <div><small>Level: ${item.skill_level || 'N/A'} | ${item.notes || 'No notes'}</small></div>
            `;
        } else if (type === 'Modules') {
            content = `
                <strong>${item.title}</strong>
                <div><small>${item.description || 'No description'}</small></div>
            `;
        } else if (type === 'Users') {
            content = `
                <strong>${item.name} ${item.surname}</strong>
                <div><small>${item.email} | ${item.usertype}</small></div>
            `;
        } else if (type === 'Locations') {
            content = `
                <strong>${item.code}</strong>
                <div><small>${item.name}</small></div>
            `;
        } else if (type === 'Divisions') {
            content = `
                <strong>${item.code}</strong>
                <div><small>${item.name}</small></div>
            `;
        } else if (type === 'Warehouses') {
            content = `
                <strong>${item.code}</strong>
                <div><small>${item.name}</small></div>
            `;
        } else if (type === 'Laboratories') {
            content = `
                <strong>${item.code}</strong>
                <div><small>${item.name}</small></div>
            `;
        } else if (type === 'Materials') {
            content = `
                <strong>${item.materialName}</strong>
                <div><small>${item.materialtype}</small></div>
            `;
        } else if (type === 'Tools') {
            content = `
                <strong>${item.name}</strong>
                <div><small>${item.tai_number}</small></div>
            `;
        } else if (type === 'Equipments') {
            content = `
                <strong>${item.name}</strong>
                <div><small>${item.tai_number}</small></div>
            `;
        } else if (type === 'Parameters') {
            content = `
                <strong>${item.name}</strong>
                <div><small>${item.parametertype} | ${item.unit}</small></div>
            `;
        } else if (type === 'TestStandards') {
            content = `
                <strong>${item.name}</strong>
            `;
        }

        li.innerHTML = content;
        return li;
    }

    // Common modal display
    async showModal(mode, params = null) {
        this.currentMode = mode;
        const elements = this.getFormElements();

        if (!this.modal) {
            this.modal = bootstrap.Modal.getOrCreateInstance(
                document.getElementById(elements.modal)
            );
        }

        // Initialize Tom Select instances
        await this.initializeTomSelects();

        const form = document.getElementById(elements.form);
        const titleEl = document.getElementById(elements.modalTitle);
        const primaryEl = document.getElementById(elements.saveBtn);

        if (mode === "add") {
            titleEl.textContent = `Add New ${this.getEntityName()}`;
            primaryEl.innerHTML = `<i class="fas fa-plus me-1"></i>Create ${this.getEntityName()}`;
            this.resetForm();
            this.updateFormVisibility();
        } else {
            this.gridDiv.__pendingEdit = {
                id: this.getUpdateIdentifier(params.data),
                rowData: params.data,
                rowNode: params.node
            };

            titleEl.textContent = `Edit ${this.getEntityName()}`;
            primaryEl.innerHTML = `<i class="fas fa-save me-1"></i>Save Changes`;
            this.populateForm(params.data);
            this.updateFormVisibility();
        }
        this.modal.show();
    }

    // Common delete confirmation
    showDeleteConfirmation(params) {
        this.gridDiv.__pendingDelete = {
            id: this.getUpdateIdentifier(params.data),
            rowNode: params.node
        };

        const modal = bootstrap.Modal.getOrCreateInstance(
            document.getElementById("confirmDeleteModal")
        );
        modal.show();
    }

    // Common save new handler
    async handleSaveNew() {
        const formData = this.getFormData();

        if (!this.validateFormData(formData)) {
            return;
        }
        console.log("Creating from this formdata:", formData)

        try {
            const response = await fetch(this.getApiUrls().create, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(formData)
            });

            if (response.ok) {
                const newItem = await response.json();
                const success = this.addNew(newItem);

                if (!success) {
                    console.warn(`Failed to add ${this.getEntityName()} to grid, reloading grid data`);
                    await this.loadData();
                }

                this.closeModal();
                this.resetForm();
            } else {
                const errorText = await response.text();
                alert(`Failed to create ${this.getEntityName()}: ` + errorText);
            }
        } catch (error) {
            this.handleError(`Error creating ${this.getEntityName()}`, error);
        }
    }

    // Common save edit handler
    async handleSaveEdit() {
        const pending = this.gridDiv.__pendingEdit;
        if (!pending) return;

        const formData = this.getFormData();
        console.log("Updating from this formdata:", formData)
        if (!this.validateFormData(formData, true)) {
            return;
        }

        try {
            const updateUrl = this.getApiUrls().update.replace('PLACEHOLDER', pending.id);

            const response = await fetch(updateUrl, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(formData)
            });

            if (response.ok) {
                const updatedItem = await response.json();
                const updatedData = { ...pending.rowData, ...updatedItem };

                this.gridDiv.__agGridInstance.applyTransaction({ update: [updatedData] });
            } else {
                const errorText = await response.text();
                alert(`Failed to update ${this.getEntityName()}: ` + errorText);
            }
        } catch (error) {
            this.handleError(`Error updating ${this.getEntityName()}`, error);
        } finally {
            this.closeModal();
            delete this.gridDiv.__pendingEdit;
        }
    }

    // Common delete handler
    async handleConfirmDelete() {
        const pending = this.gridDiv.__pendingDelete;
        if (!pending) return;

        try {
            const deleteUrl = this.getApiUrls().delete.replace('PLACEHOLDER', pending.id);

            const response = await fetch(deleteUrl, { method: "DELETE" });

            if (response.status === 204 || response.ok) {
                this.gridDiv.__agGridInstance.applyTransaction({
                    remove: [pending.rowNode.data]
                });
            } else {
                const errorText = await response.text();
                alert("Delete failed: " + errorText);
            }
        } catch (error) {
            this.handleError(`Error deleting ${this.getEntityName()}`, error);
        } finally {
            this.closeModal("confirmDeleteModal");
            delete this.gridDiv.__pendingDelete;
        }
    }

    // Common modal closing
    closeModal(modalId = null) {
        const targetModalId = modalId || this.getFormElements().modal;
        const modal = bootstrap.Modal.getInstance(document.getElementById(targetModalId));
        if (modal) {
            modal.hide();
        }
        this.destroyTomSelects();
    }

    // Common form reset
    resetForm() {
        const formElement = document.getElementById(this.getFormElements().form);
        if (formElement) {
            formElement.reset();
        }
        this.clearForm();
    }

    // Common add new item to grid
    addNew(newItem) {
        if (!this.gridDiv.__agGridInstance) {
            console.error("Grid instance not found");
            return false;
        }

        const currentColumnDefs = this.gridDiv.__agGridInstance.getColumnDefs();
        const isEmptyGrid = !currentColumnDefs || currentColumnDefs.length === 0;

        if (isEmptyGrid) {
            this.reinitializeGridWithData([newItem]);
        } else {
            this.gridDiv.__agGridInstance.applyTransaction({ add: [newItem] });
        }

        return true;
    }

    // Common grid reinitialization
    reinitializeGridWithData(data) {
        const gridOptions = this.createGridOptions(data);
        this.initializeGrid(gridOptions);
    }

    // Common grid initialization
    initializeGrid(gridOptions) {
        if (this.gridDiv.__agGridInstance) {
            this.gridDiv.__agGridInstance.destroy();
        }
        this.gridDiv.__agGridInstance = agGrid.createGrid(this.gridDiv, gridOptions);
    }

    // Common error handling
    handleError(message, error) {
        console.error(message, error);
        alert("Network error - please try again.");
    }

    destroyTomSelects() {
        Object.entries(this.tomSelectInstances).forEach(([key, instance]) => {
            if (instance) {
                instance.destroy();
            }
            this.tomSelectInstances[key] = null;
        });
    }

    clearTomSelects() {
        Object.values(this.tomSelectInstances).forEach(instance => {
            if (instance) {
                instance.clear();
            }
        });
    }

    clearForm() {
        this.clearTomSelects();
    }

    // These fetch methods are used for tomselects and common in gridmanagers.
    async fetchCoatings() {
        const grid = document.querySelector("#coatings-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchLayups() {
        const grid = document.querySelector("#layups-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchLaboratories() {
        const grid = document.querySelector("#laboratories-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchWarehouses() {
        const grid = document.querySelector("#warehouses-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchEquipmentGroups() {
        const grid = document.querySelector("#equipment-groups-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchToolGroups() {
        const grid = document.querySelector("#tool-groups-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchTools() {
        const grid = document.querySelector("#tools-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchEquipments() {
        const grid = document.querySelector("#equipments-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchUsers() {
        const grid = document.querySelector("#users-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchDivisions() {
        const grid = document.querySelector("#divisions-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchUserRoles() {
        const grid = document.querySelector("#user-roles-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchUserSkills() {
        const grid = document.querySelector("#user-skills-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchExistingModules() {
        const grid = document.querySelector("#existing-modules-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchLocations() {
        const grid = document.querySelector("#locations-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchPedigreePrepregs() {
        const grid = document.querySelector("#pedigree-prepreg-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchPedigreeMetals() {
        const grid = document.querySelector("#pedigree-metal-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchPedigreeCores() {
        const grid = document.querySelector("#pedigree-core-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchPedigreeAdhesives() {
        const grid = document.querySelector("#pedigree-adhesive-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchTestStandards() {
        const grid = document.querySelector("#teststandards-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
    async fetchParameters() {
        const grid = document.querySelector("#parameters-url");
        return await this.fetchData(grid.dataset.urlGetAll)
    }
}