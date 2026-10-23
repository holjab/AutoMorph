/**
 * AutoMorph Management Dashboard — Client Application
 * Interacts with FastAPI backend for metrics, live SSE log streaming,
 * and multi-step YAML configuration wizards.
 */

'use strict';

// ---------------------------------------------------------------------------
// Global Application State
// ---------------------------------------------------------------------------
const state = {
  status: null,
  config: null,
  configuredApps: [],
  globalRepos: [],
  activeTab: 'apps',
  activeSearch: '',
  appFilter: 'all',
  currentFilesPath: '',
  currentResultJson: null,
  cachedFilesData: null,
  eventSource: null,
  isStreaming: false,
  streamStartTime: null,
  streamTimerInterval: null,
  updateInfo: null,

  wizard: {
    step: 1,
    selectedPackage: '',
    selectedAppName: '',
    selectedIconUrl: '',
    isCustomPackage: false,
    suggestedRepos: [],
    repos: new Set(),
    aliases: [],
  },

  wizardPatches: {
    repoPatches: {}, // owner -> { repoUrl, owner, patches: [] }
    selectedRepoOwner: 'all',
    patchFilter: 'all',
    scopeFilter: 'all',
    patchSearch: '',
  },

  editApp: {
    package: '',
    appName: '',
    iconUrl: '',
    repos: new Set(),
    aliases: [],
    activeTab: 'repos',
    repoPatches: {}, // owner -> { repoUrl, owner, patches: [] }
    selectedRepoOwner: 'all',
    patchFilter: 'all',
    scopeFilter: 'all',
    patchSearch: '',
  },

  settings: null,
  emailWizard: {
    step: 1,
    recipients: [],
  },

  ntfyWizard: {
    step: 1,
  }
};

// ---------------------------------------------------------------------------
// Duration & Time Formatting Helpers
// ---------------------------------------------------------------------------
function formatDuration(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined || totalSeconds === '' || isNaN(totalSeconds)) return '--';
  const sec = Math.max(0, Math.round(Number(totalSeconds)));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) {
    return `${h}h ${m}m ${s}s`;
  }
  if (m > 0) {
    return `${m}m ${s}s`;
  }
  return `${s}s`;
}

// ---------------------------------------------------------------------------
// Initialization & Lifecycle
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  document.body.classList.add('app-ready');
  initTheme();
  setupEventListeners();
  loadAllData();

  // Background status poll every 10 seconds
  setInterval(loadStatus, 10000);

  // Background update check on startup and every 30 minutes
  checkAppUpdates(false);
  setInterval(() => checkAppUpdates(false), 30 * 60 * 1000);

  // Hash routing for deep links (e.g., #/files or #/files/pkg)
  handleHashRouting();
  window.addEventListener('hashchange', handleHashRouting);
});

function initTheme() {
  const saved = localStorage.getItem('automorph-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcon(saved);
}

function updateThemeIcon(theme) {
  const icon = document.getElementById('theme-icon');
  if (icon) {
    icon.textContent = theme === 'dark' ? 'light_mode' : 'dark_mode';
  }
}

// ---------------------------------------------------------------------------
// Event Listeners Setup
// ---------------------------------------------------------------------------
function setupEventListeners() {
  // Theme Toggle
  document.getElementById('btn-theme-toggle')?.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('automorph-theme', next);
    updateThemeIcon(next);
  });

  // Refresh Button
  document.getElementById('btn-refresh-status')?.addEventListener('click', () => {
    loadAllData(true);
  });

  // Docker Image Update Modal Controls
  document.getElementById('site-update-btn')?.addEventListener('click', openUpdateModal);
  document.getElementById('site-version-tag')?.addEventListener('click', openUpdateModal);
  document.getElementById('btn-update-modal-close')?.addEventListener('click', closeUpdateModal);
  document.getElementById('btn-update-modal-done')?.addEventListener('click', closeUpdateModal);
  document.getElementById('modal-docker-update')?.addEventListener('click', (e) => {
    if (e.target.id === 'modal-docker-update') closeUpdateModal();
  });
  document.getElementById('btn-update-check-again')?.addEventListener('click', () => {
    checkAppUpdates(true);
  });
  document.getElementById('btn-update-copy-cmd')?.addEventListener('click', () => {
    const cmd = document.getElementById('update-modal-cmd')?.textContent || 'docker compose pull && docker compose up -d';
    navigator.clipboard.writeText(cmd.trim()).then(() => {
      showToast('Upgrade command copied to clipboard!', 'success');
    }).catch(() => {
      showToast('Failed to copy command', 'error');
    });
  });

  // Tabs Switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      if (tab === 'files') {
        switchTab('files', '');
      } else {
        switchTab(tab);
      }
    });
  });

  // Settings Open Downloads Button
  document.getElementById('btn-open-files')?.addEventListener('click', () => {
    switchTab('files', '');
  });
  document.getElementById('btn-browse-files')?.addEventListener('click', () => {
    switchTab('files', '');
  });

  // Files Tab Controls
  document.getElementById('btn-files-refresh')?.addEventListener('click', () => {
    loadFiles(state.currentFilesPath || '');
  });
  document.getElementById('btn-files-root')?.addEventListener('click', () => {
    loadFiles('');
  });
  document.getElementById('files-filter-input')?.addEventListener('input', () => {
    filterFilesTable();
  });

  // Result Viewer Modal Controls
  document.querySelectorAll('#modal-result-viewer .modal-tab-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.resTab;
      if (tab) switchResultModalTab(tab);
    });
  });
  document.getElementById('btn-res-modal-close')?.addEventListener('click', closeResultViewerModal);
  document.getElementById('btn-res-modal-close-x')?.addEventListener('click', closeResultViewerModal);
  document.getElementById('modal-result-viewer')?.addEventListener('click', (e) => {
    if (e.target.id === 'modal-result-viewer') closeResultViewerModal();
  });
  document.getElementById('btn-res-modal-copy')?.addEventListener('click', () => {
    if (!state.currentResultJson) return;
    const jsonStr = JSON.stringify(state.currentResultJson, null, 2);
    navigator.clipboard.writeText(jsonStr).then(() => {
      showToast('result.json copied to clipboard!', 'success');
    }).catch(() => {
      showToast('Failed to copy to clipboard', 'error');
    });
  });

  // App Search Input
  document.getElementById('apps-search-input')?.addEventListener('input', (e) => {
    state.activeSearch = e.target.value.toLowerCase().trim();
    renderConfiguredApps();
  });

  // App Active/Disabled Filter Pills
  document.querySelectorAll('.filter-pill[data-app-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      state.appFilter = btn.dataset.appFilter;
      document.querySelectorAll('.filter-pill[data-app-filter]').forEach(b => {
        b.classList.toggle('active', b.dataset.appFilter === state.appFilter);
      });
      renderConfiguredApps();
    });
  });

  // Pipeline Run & Console Actions
  document.getElementById('btn-run-pipeline')?.addEventListener('click', triggerPipelineRun);
  document.getElementById('btn-view-console')?.addEventListener('click', openTerminalAndConnect);
  document.getElementById('status-badge')?.addEventListener('click', () => {
    if (state.status?.is_running) {
      openTerminalAndConnect();
    }
  });
  document.getElementById('btn-banner-open-console')?.addEventListener('click', openTerminalAndConnect);

  // Last Run Stat Card Click & Keyboard Trigger
  const lastRunCard = document.getElementById('stat-card-last-run');
  if (lastRunCard) {
    lastRunCard.addEventListener('click', openLastRunLogModal);
    lastRunCard.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openLastRunLogModal();
      }
    });
  }

  // Terminal Modal Actions
  document.getElementById('btn-term-close')?.addEventListener('click', closeTerminalModal);
  document.getElementById('btn-term-copy')?.addEventListener('click', copyTerminalOutput);
  document.getElementById('btn-term-stop')?.addEventListener('click', () => stopRunningPipeline());

  // Wizard Launch & Nav
  document.getElementById('btn-add-app-nav')?.addEventListener('click', openAddPackageWizard);
  document.getElementById('btn-wiz-cancel')?.addEventListener('click', closeWizardModal);
  document.getElementById('btn-wiz-back')?.addEventListener('click', wizardPrevStep);
  document.getElementById('btn-wiz-next')?.addEventListener('click', wizardNextStep);
  document.getElementById('btn-wiz-save')?.addEventListener('click', wizardSubmitApp);

  // Wizard Step 1: Catalog Search & Custom Toggle
  let catalogSearchTimer = null;
  document.getElementById('wiz-search-input')?.addEventListener('input', (e) => {
    clearTimeout(catalogSearchTimer);
    catalogSearchTimer = setTimeout(() => {
      searchCatalog(e.target.value.trim());
    }, 250);
  });

  document.getElementById('btn-wiz-refresh-catalog')?.addEventListener('click', async () => {
    showToast('Refreshing live catalog from Morphe...', 'info');
    await fetchMorpheCatalog(true);
    const query = document.getElementById('wiz-search-input')?.value.trim() || '';
    searchCatalog(query);
    showToast('Live catalog refreshed', 'success');
  });

  document.getElementById('btn-wiz-toggle-custom')?.addEventListener('click', () => {
    const wrap = document.getElementById('wiz-custom-pkg-wrap');
    state.wizard.isCustomPackage = !state.wizard.isCustomPackage;
    if (state.wizard.isCustomPackage) {
      wrap.classList.remove('hidden');
      document.getElementById('wiz-catalog-results').classList.add('hidden');
      document.getElementById('btn-wiz-toggle-custom').classList.add('btn-primary');
    } else {
      wrap.classList.add('hidden');
      document.getElementById('wiz-catalog-results').classList.remove('hidden');
      document.getElementById('btn-wiz-toggle-custom').classList.remove('btn-primary');
    }
  });

  // Wizard Step 2: Custom Repo Add
  document.getElementById('btn-wiz-add-custom-repo')?.addEventListener('click', () => {
    const input = document.getElementById('wiz-custom-repo-input');
    const url = input.value.trim();
    if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
      addSuggestedRepoItem({ repo_url: url, name: url, patch_count: 'Custom' }, true);
      input.value = '';
    } else {
      showToast('Please enter a valid Git repository URL.', 'warning');
    }
  });

  // Wizard Step 4: Add Alias Mapping
  document.getElementById('btn-wiz-add-alias')?.addEventListener('click', () => {
    addAliasRow();
  });

  // Wizard Step 3 (Patches Toolbar)
  document.getElementById('wiz-patch-search')?.addEventListener('input', (e) => {
    state.wizardPatches.patchSearch = e.target.value.toLowerCase().trim();
    renderWizardPatchesList();
  });

  document.getElementById('wiz-patch-repo-filter')?.addEventListener('change', (e) => {
    state.wizardPatches.selectedRepoOwner = e.target.value;
    renderWizardPatchesList();
  });

  document.querySelectorAll('#wiz-step-3 [data-wiz-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#wiz-step-3 [data-wiz-filter]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.wizardPatches.patchFilter = btn.dataset.wizFilter || 'all';
      renderWizardPatchesList();
    });
  });

  document.querySelectorAll('#wiz-step-3 [data-wiz-scope]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#wiz-step-3 [data-wiz-scope]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.wizardPatches.scopeFilter = btn.dataset.wizScope || 'all';
      renderWizardPatchesList();
    });
  });

  document.getElementById('btn-wiz-download-options')?.addEventListener('click', () => {
    handleSeeAvailablePatches(true);
  });

  document.getElementById('btn-wiz-patches-defaults')?.addEventListener('click', () => {
    setWizardPatchesDefault();
  });

  document.getElementById('btn-wiz-patches-enable-all')?.addEventListener('click', () => {
    setWizardPatchesAll(true);
  });

  document.getElementById('btn-wiz-patches-disable-all')?.addEventListener('click', () => {
    setWizardPatchesAll(false);
  });

  // Edit App Modal Actions
  document.getElementById('btn-edit-close')?.addEventListener('click', closeEditAppModal);
  document.getElementById('btn-edit-cancel')?.addEventListener('click', closeEditAppModal);
  document.getElementById('btn-edit-save')?.addEventListener('click', saveEditAppModal);

  document.querySelectorAll('#modal-edit-app .modal-tab-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.editTab;
      if (tab) switchEditTab(tab);
    });
  });

  document.getElementById('btn-edit-add-custom-repo')?.addEventListener('click', () => {
    const input = document.getElementById('edit-custom-repo-input');
    const url = input?.value.trim();
    if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
      addEditSuggestedRepoItem({ repo_url: url, name: url, patch_count: 'Custom' }, true);
      input.value = '';
    } else {
      showToast('Please enter a valid Git repository URL.', 'warning');
    }
  });

  document.getElementById('btn-edit-add-alias')?.addEventListener('click', () => {
    addEditAliasRow();
  });

  document.getElementById('edit-patch-repo-filter')?.addEventListener('change', (e) => {
    state.editApp.selectedRepoOwner = e.target.value;
    renderEditPatchesList();
  });

  document.getElementById('edit-patch-search')?.addEventListener('input', (e) => {
    state.editApp.patchSearch = e.target.value.toLowerCase().trim();
    renderEditPatchesList();
  });

  document.querySelectorAll('#modal-edit-app [data-edit-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#modal-edit-app [data-edit-filter]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.editApp.patchFilter = btn.dataset.editFilter || 'all';
      renderEditPatchesList();
    });
  });

  document.querySelectorAll('#modal-edit-app [data-edit-scope]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#modal-edit-app [data-edit-scope]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.editApp.scopeFilter = btn.dataset.editScope || 'all';
      renderEditPatchesList();
    });
  });

  document.getElementById('btn-edit-download-options')?.addEventListener('click', () => {
    handleSeeAvailablePatches(false);
  });

  document.getElementById('btn-edit-patches-defaults')?.addEventListener('click', () => {
    setEditPatchesDefault();
  });

  document.getElementById('btn-edit-patches-enable-all')?.addEventListener('click', () => {
    setEditPatchesAll(true);
  });

  document.getElementById('btn-edit-patches-disable-all')?.addEventListener('click', () => {
    setEditPatchesAll(false);
  });

  // Repos Tab Actions
  document.getElementById('btn-add-repo')?.addEventListener('click', promptAddGlobalRepo);

  // Discover Available Patches Modal Actions
  document.getElementById('btn-patch-fetch-close')?.addEventListener('click', closePatchFetchModal);
  document.getElementById('btn-patch-fetch-done')?.addEventListener('click', closePatchFetchModal);
  document.getElementById('btn-patch-fetch-retry')?.addEventListener('click', () => {
    if (state.lastPatchFetchContext) {
      handleSeeAvailablePatches(state.lastPatchFetchContext.isWizard, true);
    }
  });
  document.getElementById('btn-patch-fetch-copy-log')?.addEventListener('click', copyPatchFetchLogs);
  document.getElementById('btn-patch-fetch-clear-log')?.addEventListener('click', clearPatchFetchLogs);
  // Settings Tab Actions
  document.getElementById('btn-reload-settings')?.addEventListener('click', () => loadSettings(true));
  document.getElementById('btn-save-system-settings')?.addEventListener('click', saveSystemSettings);
  document.getElementById('btn-ntfy-save')?.addEventListener('click', saveNtfySettings);
  document.getElementById('btn-ntfy-test')?.addEventListener('click', () => sendTestNtfy('btn-ntfy-test'));
  document.getElementById('btn-ntfy-wiz-test')?.addEventListener('click', () => sendTestNtfy('btn-ntfy-wiz-test'));
  document.getElementById('btn-open-ntfy')?.addEventListener('click', openNtfyModal);
  document.getElementById('btn-ntfy-modal-close')?.addEventListener('click', closeNtfyModal);
  document.getElementById('btn-ntfy-cancel')?.addEventListener('click', closeNtfyModal);
  document.getElementById('btn-ntfy-wiz-next')?.addEventListener('click', ntfyWizNext);
  document.getElementById('btn-ntfy-wiz-back')?.addEventListener('click', ntfyWizPrev);
  document.querySelectorAll('.ntfy-preset-btn').forEach((btn) => {
    btn.addEventListener('click', () => applyNtfyPreset(btn.dataset.ntfyPreset || ''));
  });
  document.querySelectorAll('#modal-ntfy .step-indicator').forEach((ind, idx) => {
    ind.addEventListener('click', () => {
      state.ntfyWizard.step = idx + 1;
      renderNtfyWizardStep();
    });
  });
  document.getElementById('modal-ntfy')?.addEventListener('click', (e) => {
    if (e.target.id === 'modal-ntfy') closeNtfyModal();
  });
  loadNtfyPresets();
  document.getElementById('btn-email-test-quick')?.addEventListener('click', sendQuickTestEmail);

  const chkMaxOrig = document.getElementById('setting-max-original-enable');
  const inpMaxOrig = document.getElementById('setting-max-original-packages');
  chkMaxOrig?.addEventListener('change', () => {
    if (inpMaxOrig) {
      inpMaxOrig.disabled = !chkMaxOrig.checked;
      if (chkMaxOrig.checked) {
        if (!inpMaxOrig.value || parseInt(inpMaxOrig.value, 10) <= 0) {
          inpMaxOrig.value = '3';
        }
        inpMaxOrig.focus();
      } else {
        inpMaxOrig.value = '';
      }
    }
  });

  const chkMaxPatched = document.getElementById('setting-max-patched-enable');
  const inpMaxPatched = document.getElementById('setting-max-patched-packages');
  chkMaxPatched?.addEventListener('change', () => {
    if (inpMaxPatched) {
      inpMaxPatched.disabled = !chkMaxPatched.checked;
      if (chkMaxPatched.checked) {
        if (!inpMaxPatched.value || parseInt(inpMaxPatched.value, 10) <= 0) {
          inpMaxPatched.value = '3';
        }
        inpMaxPatched.focus();
      } else {
        inpMaxPatched.value = '';
      }
    }
  });

  // Email Wizard Actions
  document.getElementById('btn-open-email-wizard')?.addEventListener('click', openEmailWizard);
  document.getElementById('btn-email-wiz-close')?.addEventListener('click', closeEmailWizard);
  document.getElementById('btn-email-wiz-cancel')?.addEventListener('click', closeEmailWizard);
  document.getElementById('btn-email-wiz-next')?.addEventListener('click', emailWizNextStep);
  document.getElementById('btn-email-wiz-back')?.addEventListener('click', emailWizPrevStep);
  document.getElementById('btn-email-wiz-save')?.addEventListener('click', saveEmailWizard);
  document.getElementById('btn-email-wiz-test')?.addEventListener('click', sendTestEmailFromWizard);
  document.getElementById('btn-email-wiz-add-recipient')?.addEventListener('click', addEmailWizRecipient);
  document.getElementById('email-wiz-new-recipient')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addEmailWizRecipient();
    }
  });
  document.getElementById('email-wiz-subject-prefix')?.addEventListener('input', updateEmailSubjectPreview);

  document.querySelectorAll('.email-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const preset = btn.dataset.preset;
      applyEmailPreset(preset);
    });
  });

  // Password Visibility Toggles
  document.querySelectorAll('.btn-toggle-pwd').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      const input = document.getElementById(targetId);
      if (!input) return;
      const isPwd = input.type === 'password';
      input.type = isPwd ? 'text' : 'password';
      const icon = btn.querySelector('.material-symbols-rounded');
      if (icon) {
        icon.textContent = isPwd ? 'visibility_off' : 'visibility';
      }
    });
  });

  document.querySelectorAll('#modal-email-wizard .step-indicator').forEach((ind, idx) => {
    ind.addEventListener('click', () => {
      state.emailWizard.step = idx + 1;
      renderEmailWizardStep();
    });
  });

  document.getElementById('modal-email-wizard')?.addEventListener('click', (e) => {
    if (e.target.id === 'modal-email-wizard') closeEmailWizard();
  });
}

// ---------------------------------------------------------------------------
// Data Fetching & Dashboard Stats
// ---------------------------------------------------------------------------
async function loadAllData(isManualClick = false) {
  const btn = document.getElementById('btn-refresh-status');
  const icon = btn?.querySelector('.material-symbols-rounded');

  if (isManualClick && btn) {
    btn.disabled = true;
    if (icon) icon.style.animation = 'spin 0.8s linear infinite';
  }

  try {
    await Promise.all([loadStatus(), loadConfig(), loadSettings()]);

    // Background resolve of Morphe catalog to populate official app icons and names
    fetchMorpheCatalog().then(catalog => {
      if (catalog) {
        renderConfiguredApps();
      }
    });

    if (isManualClick) {
      showToast('Dashboard refreshed', 'success');
    }
  } catch (err) {
    if (isManualClick) {
      showToast('Failed to refresh dashboard: ' + err.message, 'error');
    }
  } finally {
    if (isManualClick && btn) {
      setTimeout(() => {
        btn.disabled = false;
        if (icon) icon.style.animation = '';
      }, 300);
    }
  }
}

async function loadStatus() {
  try {
    const res = await fetch(`/api/status?_t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Status load failed');
    const data = await res.json();

    const wasRunning = state.status?.is_running;
    const prevFinishedAt = state.status?.last_run?.finished_at;
    const newFinishedAt = data.last_run?.finished_at;
    const prevLastRunId = state.status?.last_run?.run_id;
    const newLastRunId = data.last_run?.run_id;

    // Detect if a run just completed while polling or in the background
    const runJustFinished = Boolean(
      (wasRunning && !data.is_running) ||
      (newFinishedAt && prevFinishedAt && newFinishedAt !== prevFinishedAt) ||
      (newLastRunId && prevLastRunId && newLastRunId !== prevLastRunId)
    );

    state.status = data;
    renderStatus(data);

    if (runJustFinished) {
      loadConfig();
    }
  } catch (err) {
    console.warn('Could not load status:', err);
    updateStatusBadge('Offline', 'badge-danger');
  }
}

async function loadConfig() {
  try {
    const res = await fetch(`/api/config?_t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Config load failed');
    const data = await res.json();
    state.config = data;
    const packages = data.packages || data.apps || [];
    state.configuredApps = packages;

    const discoveredRepos = new Set();
    if (Array.isArray(data.patch_repos)) {
      data.patch_repos.forEach(r => {
        if (r && typeof r === 'string') discoveredRepos.add(r.trim());
      });
    }
    packages.forEach(pkg => {
      (pkg.repos || []).forEach(r => {
        if (r && typeof r === 'string') discoveredRepos.add(r.trim());
      });
    });
    state.globalRepos = Array.from(discoveredRepos);

    renderConfiguredApps();
    renderGlobalRepos();
  } catch (err) {
    console.error('Could not load config:', err);
    showToast('Failed to load config.yaml', 'error');
  }
}

function renderStatus(data) {
  // 1. Navigation status badge & active banner
  const viewConsoleBtn = document.getElementById('btn-view-console');
  const activeBanner = document.getElementById('active-run-banner');
  const bannerSource = document.getElementById('active-run-banner-source');

  if (data.is_running) {
    const triggerInfo = getRunTriggerInfo(data.active_run || data.active_lock);
    updateStatusBadge(triggerInfo.navBadgeText, 'badge-pulse badge-warning');
    if (viewConsoleBtn) viewConsoleBtn.classList.remove('hidden');
    if (activeBanner) {
      activeBanner.classList.remove('hidden');
      const runId = (data.active_run?.run_id || data.active_lock?.run_id || 'active');
      if (bannerSource) bannerSource.textContent = `Triggered by ${triggerInfo.label} (${runId})`;
    }
  } else {
    updateStatusBadge('Idle & Ready', 'badge-success');
    if (viewConsoleBtn) viewConsoleBtn.classList.add('hidden');
    if (activeBanner) activeBanner.classList.add('hidden');
  }

  // 2. Next scheduled run card
  const nextRun = data.next_run;
  if (nextRun) {
    document.getElementById('stat-next-run').textContent = nextRun.countdown_formatted;
    document.getElementById('stat-next-sub').textContent = `Daily at ${nextRun.schedule_time} (${nextRun.display_time})`;
  }

  // 3. Last run card
  const lastRun = data.last_run;
  const lastStatusEl = document.getElementById('stat-last-status');
  const lastSubEl = document.getElementById('stat-last-sub');
  const lastIconWrap = document.getElementById('stat-last-icon-wrap');

  if (lastRun) {
    const isSuccess = lastRun.status === 'success';
    lastStatusEl.textContent = isSuccess ? 'Success' : 'Failed';
    lastStatusEl.style.color = isSuccess ? 'var(--color-success)' : 'var(--color-danger)';
    const durStr = lastRun.duration_formatted || formatDuration(lastRun.duration_seconds || lastRun.duration);
    lastSubEl.textContent = `Duration: ${durStr} (${lastRun.source || 'pipeline'})`;
    lastIconWrap.style.color = isSuccess ? 'var(--color-success)' : 'var(--color-danger)';
  } else {
    lastStatusEl.textContent = 'None Yet';
    lastSubEl.textContent = 'Waiting for first execution';
  }

  // 4. Patched Storage card
  const storage = data.storage;
  if (storage) {
    document.getElementById('stat-storage-size').textContent = storage.formatted_size;
    document.getElementById('stat-apk-count').textContent = `${storage.apk_count} ready APK files`;
  }

  // 5. Config totals
  const stats = data.stats;
  if (stats) {
    document.getElementById('stat-apps-count').textContent = `${stats.configured_apps} Configured`;
    document.getElementById('stat-repos-count').textContent = `${stats.connected_repos} Active Repos`;
  }

  // 6. Version Info
  const version = data.version;
  const versionValEl = document.getElementById('site-version-val');
  if (version && versionValEl) {
    versionValEl.textContent = `v${version.version || '0.01'}`;
    const versionTagEl = document.getElementById('site-version-tag');
    if (versionTagEl && version.branch) {
      versionTagEl.title = `AutoMorph v${version.version} (${version.branch}) — Click to view update details`;
    }
  }
}

// ---------------------------------------------------------------------------
// Docker Image Update Management
// ---------------------------------------------------------------------------
async function checkAppUpdates(force = false) {
  const checkBtn = document.getElementById('btn-update-check-again');
  const checkIcon = document.getElementById('icon-update-check-again');
  const errorBox = document.getElementById('update-modal-error-box');
  const errorText = document.getElementById('update-modal-error-text');

  if (force && checkBtn) {
    checkBtn.disabled = true;
    if (checkIcon) checkIcon.style.animation = 'spin 0.8s linear infinite';
  }

  try {
    const res = await fetch(`/api/updates/check?force=${force}&_t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);
    const data = await res.json();
    state.updateInfo = data;

    // Update Footer Badge
    const updateBtn = document.getElementById('site-update-btn');
    const updateBtnText = document.getElementById('site-update-btn-text');
    if (updateBtn) {
      if (data.update_available === true) {
        updateBtn.classList.remove('hidden');
        updateBtn.style.display = 'inline-flex';
        if (updateBtnText) {
          const verLabel = data.latest_tag || (data.latest_version ? `v${data.latest_version}` : '');
          updateBtnText.textContent = data.is_prerelease
            ? `Update: ${verLabel} (pre-release)`
            : `Update: ${verLabel}`;
        }
      } else {
        updateBtn.classList.add('hidden');
        updateBtn.style.display = 'none';
      }
    }

    // Render modal if currently open
    const modal = document.getElementById('modal-docker-update');
    if (modal && modal.classList.contains('open')) {
      renderUpdateModal(data);
    }

    return data;
  } catch (err) {
    console.warn('Failed to check for updates:', err);
    if (errorBox && errorText) {
      errorText.textContent = `Couldn't reach GitHub: ${err.message || 'Network error'}`;
      errorBox.style.display = 'block';
    }
  } finally {
    if (force && checkBtn) {
      setTimeout(() => {
        checkBtn.disabled = false;
        if (checkIcon) checkIcon.style.animation = '';
      }, 500);
    }
  }
}

function openUpdateModal() {
  const modal = document.getElementById('modal-docker-update');
  if (!modal) return;
  modal.classList.add('open');

  if (state.updateInfo) {
    renderUpdateModal(state.updateInfo);
  } else {
    checkAppUpdates(false);
  }
}

function closeUpdateModal() {
  const modal = document.getElementById('modal-docker-update');
  if (modal) modal.classList.remove('open');
}

function renderUpdateModal(data) {
  if (!data) return;

  const branchBadge = document.getElementById('update-modal-branch-badge');
  const typeBadge = document.getElementById('update-modal-type-badge');
  const banner = document.getElementById('update-modal-banner');
  const bannerIcon = document.getElementById('update-modal-banner-icon');
  const bannerText = document.getElementById('update-modal-banner-text');

  const currentBuild = document.getElementById('update-modal-current-build');
  const currentSha = document.getElementById('update-modal-current-sha');
  const latestBuild = document.getElementById('update-modal-latest-build');
  const latestSha = document.getElementById('update-modal-latest-sha');

  const releaseTitle = document.getElementById('update-modal-release-title');
  const releaseNotes = document.getElementById('update-modal-commit-msg');
  const commitLink = document.getElementById('update-modal-commit-link');
  const publishedAt = document.getElementById('update-modal-published-at');
  const checkedAt = document.getElementById('update-modal-checked-at');

  const errorBox = document.getElementById('update-modal-error-box');
  const errorText = document.getElementById('update-modal-error-text');

  // Branch & Type badge (textContent only)
  if (branchBadge) branchBadge.textContent = data.branch || 'dev';
  if (typeBadge) {
    if (data.is_prerelease) {
      typeBadge.textContent = 'Pre-release';
      typeBadge.className = 'badge badge-warning';
      typeBadge.classList.remove('hidden');
    } else if (data.latest_tag || data.latest_version) {
      typeBadge.textContent = 'Stable';
      typeBadge.className = 'badge badge-success';
      typeBadge.classList.remove('hidden');
    } else {
      typeBadge.classList.add('hidden');
    }
  }

  // Current build info
  if (currentBuild) {
    currentBuild.textContent = data.current_tag || (data.current_version ? `v${data.current_version}` : '—');
  }
  if (currentSha) {
    currentSha.textContent = data.current_commit ? `sha: ${data.current_commit}` : (data.current_tag || '—');
  }

  // Latest published info
  if (latestBuild) {
    latestBuild.textContent = data.latest_tag || (data.latest_version ? `v${data.latest_version}` : '—');
  }
  if (latestSha) {
    latestSha.textContent = data.is_prerelease ? 'Pre-release Channel' : 'Stable Channel';
  }

  // Release title and notes rendered strictly via textContent
  if (releaseTitle) {
    releaseTitle.textContent = data.release_name || 'Release Overview';
  }
  if (releaseNotes) {
    releaseNotes.textContent = data.release_notes || 'No release notes provided.';
  }

  // URL validation
  if (commitLink) {
    const relUrl = data.release_url;
    if (typeof relUrl === 'string' && relUrl.startsWith('https://github.com/')) {
      commitLink.href = relUrl;
      commitLink.style.display = 'inline-flex';
    } else {
      commitLink.removeAttribute('href');
      commitLink.style.display = 'none';
    }
  }

  // Timestamps
  if (publishedAt) {
    publishedAt.textContent = data.published_at ? `Published: ${new Date(data.published_at).toLocaleString()}` : '';
  }
  if (checkedAt) {
    checkedAt.textContent = data.checked_at ? `Checked: ${new Date(data.checked_at).toLocaleTimeString()}` : 'Checked: Just now';
  }

  // Banner status
  if (banner && bannerText && bannerIcon) {
    if (data.update_available === true) {
      banner.style.background = 'rgba(245, 158, 11, 0.1)';
      banner.style.borderColor = 'rgba(245, 158, 11, 0.3)';
      banner.style.color = 'var(--color-warning)';
      bannerIcon.textContent = 'new_releases';
      const label = data.latest_tag || data.latest_version || 'new';
      bannerText.textContent = `A new release (${label}) is published and ready to pull!`;
    } else if (data.update_available === false) {
      banner.style.background = 'rgba(16, 185, 129, 0.1)';
      banner.style.borderColor = 'rgba(16, 185, 129, 0.3)';
      banner.style.color = 'var(--color-success)';
      bannerIcon.textContent = 'check_circle';
      bannerText.textContent = `Your container is running the latest release for the ${data.branch || 'active'} track.`;
    } else {
      banner.style.background = 'rgba(56, 189, 248, 0.1)';
      banner.style.borderColor = 'rgba(56, 189, 248, 0.3)';
      banner.style.color = 'var(--primary-cyan)';
      bannerIcon.textContent = 'info';
      bannerText.textContent = data.message || 'No release updates found.';
    }
  }

  // Error / Cooldown box
  if (errorBox && errorText) {
    if (data.cooldown) {
      errorText.textContent = `Checked recently. Please wait ${data.cooldown_remaining || 60}s before checking again.`;
      errorBox.style.background = 'rgba(245, 158, 11, 0.1)';
      errorBox.style.borderColor = 'rgba(245, 158, 11, 0.3)';
      errorBox.style.color = 'var(--color-warning)';
      errorBox.style.display = 'block';
    } else if (data.error) {
      errorText.textContent = data.error;
      errorBox.style.background = 'rgba(239, 68, 68, 0.1)';
      errorBox.style.borderColor = 'rgba(239, 68, 68, 0.3)';
      errorBox.style.color = 'var(--color-danger)';
      errorBox.style.display = 'block';
    } else {
      errorBox.style.display = 'none';
    }
  }
}

function updateStatusBadge(text, className) {
  const badge = document.getElementById('status-badge');
  const badgeText = document.getElementById('status-badge-text');
  if (badge && badgeText) {
    badge.className = `badge ${className}`;
    badgeText.textContent = text;
  }
}

// ---------------------------------------------------------------------------
// Tabs Switching
// ---------------------------------------------------------------------------
function switchTab(tabName, targetPath = null) {
  state.activeTab = tabName;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });

  document.getElementById('tab-content-apps')?.classList.toggle('hidden', tabName !== 'apps');
  document.getElementById('tab-content-repos')?.classList.toggle('hidden', tabName !== 'repos');
  document.getElementById('tab-content-settings')?.classList.toggle('hidden', tabName !== 'settings');
  document.getElementById('tab-content-files')?.classList.toggle('hidden', tabName !== 'files');

  document.getElementById('apps-search-wrap')?.classList.toggle('hidden', tabName !== 'apps');

  if (tabName === 'files') {
    const pathToLoad = targetPath !== null ? targetPath : '';
    loadFiles(pathToLoad);
  } else {
    if (window.location.hash.startsWith('#files') || window.location.hash.startsWith('#/files')) {
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }
}

// ---------------------------------------------------------------------------
// Configured Applications View & Helpers
// ---------------------------------------------------------------------------
function getAppDisplayName(pkg) {
  if (!pkg) return '';
  const configured = (state.configuredApps || []).find(a => a.package === pkg);
  if (configured && configured.name) return configured.name;

  if (typeof morpheCatalogData !== 'undefined' && morpheCatalogData && morpheCatalogData[pkg]?.name) {
    return morpheCatalogData[pkg].name;
  }

  return getFallbackAppName(pkg);
}

function getRunTriggerInfo(runOrLock) {
  if (!runOrLock) {
    return {
      source: 'manual',
      package: null,
      appName: '',
      label: 'Manual (All Apps)',
      shortLabel: 'All Apps',
      navBadgeText: 'Patching Active: All Apps',
      consoleBadgeText: 'Trigger: All Apps (Manual)',
    };
  }

  let source = runOrLock.source || 'manual';
  let pkg = runOrLock.package || null;

  // Handle composite source string like "manual:com.duolingo"
  if (typeof source === 'string' && source.includes(':') && !pkg) {
    const parts = source.split(':');
    source = parts[0];
    pkg = parts.slice(1).join(':');
  }

  let appName = '';
  if (pkg) {
    appName = getAppDisplayName(pkg);
  }

  let label = '';
  let shortLabel = '';
  let navBadgeText = '';
  let consoleBadgeText = '';

  if (pkg) {
    const targetName = appName || pkg;
    label = `Manual (${targetName})`;
    shortLabel = targetName;
    navBadgeText = `Patching Active: ${targetName}`;
    consoleBadgeText = `Trigger: ${targetName}`;
  } else if (source === 'scheduler') {
    label = 'Scheduled (All Apps)';
    shortLabel = 'Scheduled';
    navBadgeText = 'Scheduled Run: All Apps';
    consoleBadgeText = 'Trigger: Scheduled (All Apps)';
  } else {
    label = 'Manual (All Apps)';
    shortLabel = 'All Apps';
    navBadgeText = 'Patching Active: All Apps';
    consoleBadgeText = 'Trigger: All Apps (Manual)';
  }

  return {
    source,
    package: pkg,
    appName,
    label,
    shortLabel,
    navBadgeText,
    consoleBadgeText,
  };
}

function getFallbackAppName(pkg) {
  if (!pkg) return 'Unknown App';
  const parts = pkg.split('.').filter(p => !['com', 'org', 'net', 'io', 'app', 'android', 'mobile'].includes(p.toLowerCase()));
  if (parts.length > 0) {
    const main = parts[parts.length - 1];
    return main.charAt(0).toUpperCase() + main.slice(1);
  }
  const allParts = pkg.split('.');
  const last = allParts[allParts.length - 1] || pkg;
  return last.charAt(0).toUpperCase() + last.slice(1);
}

function cleanPatchErrorMessage(rawReason) {
  if (!rawReason) return { summary: 'Patch failed during execution', stack: '' };

  const text = String(rawReason).trim();
  const rawLines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);

  if (rawLines.length === 0) {
    return { summary: 'Unknown patch failure', stack: '' };
  }

  let firstLine = rawLines[0];

  // Strip Java/Kotlin exception class prefixes like "app.morphe.patcher.patch.PatchException: "
  let cleaned = firstLine.replace(/^[a-zA-Z0-9_$.]+(?:Exception|Error):\s*/i, '');

  // Clean fingerprint paths:
  // "Failed to match the fingerprint: app.morphe.patches.reddit.layout.screenshotpopup.RedditScreenshotTriggerSharingList" -> "Fingerprint mismatch: RedditScreenshotTriggerSharingList"
  if (/fingerprint/i.test(cleaned)) {
    const fpMatch = cleaned.match(/fingerprint:\s*([a-zA-Z0-9_$.]+)/i);
    if (fpMatch) {
      const parts = fpMatch[1].split('.');
      const shortName = parts[parts.length - 1];
      cleaned = `Fingerprint mismatch: ${shortName}`;
    }
  }

  // Detect stack frames
  const hasStack = rawLines.length > 1 || rawLines.some(l => l.startsWith('at ') || l.includes('.kt:') || l.includes('.java:'));
  const fullStack = hasStack ? rawLines.join('\n') : '';

  return {
    summary: cleaned || firstLine,
    stack: fullStack
  };
}

function getPatchName(p) {
  if (!p) return 'Patch';
  if (typeof p === 'string') return p;
  const raw = p.name || p.patch || p.patchName || p.patch_name || p.title || p.id;
  if (raw && typeof raw === 'object') {
    return raw.name || raw.title || 'Patch';
  }
  return raw ? String(raw).trim() : 'Patch';
}

function resolvePatchDisplayName(p, reason) {
  let name = getPatchName(p);
  if (!name || name.toLowerCase() === 'patch' || name.toLowerCase() === 'unknown' || name.toLowerCase() === 'failed patch') {
    if (reason) {
      const fpMatch = String(reason).match(/([a-zA-Z0-9_]+)Fingerprint/);
      if (fpMatch) {
        name = fpMatch[1].replace(/([a-z])([A-Z])/g, '$1 $2');
      } else {
        const patchMatch = String(reason).match(/patches\.[a-zA-Z0-9_.]+\.([a-zA-Z0-9_]+?)(?:Patch)?(?:\s|$|:|\()/);
        if (patchMatch) {
          name = patchMatch[1].replace(/([a-z])([A-Z])/g, '$1 $2');
        }
      }
    }
  }
  return name || 'Patch';
}

function resolvePatchOwner(p, patchOwnersMap) {
  if (p && typeof p === 'object' && p.owner) {
    return cleanRepoSlug(p.owner).split('/')[0];
  }
  const name = typeof p === 'string' ? p : getPatchName(p);
  if (patchOwnersMap && patchOwnersMap[name]) {
    return cleanRepoSlug(patchOwnersMap[name]).split('/')[0];
  }
  return '';
}

function formatPatchOwnerChip(owner) {
  if (!owner) return '';
  const url = formatRepoUrl(owner);
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="drawer-patch-owner-chip clickable" title="Repository Owner: ${escapeHtml(owner)} (Click to open)" onclick="event.stopPropagation()"><span class="material-symbols-rounded" style="font-size: 11px;">person</span> ${escapeHtml(owner)}</a>`;
}

function renderAttemptCard(att, idx, totalAttempts, patchOwnersMap) {
  const attNum = att.attempt_number || (idx + 1);
  const ver = att.version || 'target';
  const stratNum = att.strategy || att.run || 1;
  const stratName = att.strategy_name || (stratNum === 1 ? 'Strategy 1 (All Repos)' : 'Strategy 2 (Version-Compatible)');
  const stage = att.stage || 'Morphe Patching';
  const status = att.status || 'failed';

  let statusBadge = '';
  if (status === 'success') {
    statusBadge = `<span class="badge badge-success" style="font-size: 0.6875rem;"><span class="material-symbols-rounded" style="font-size: 11px;">check_circle</span> Built</span>`;
  } else if (status === 'partial') {
    statusBadge = `<span class="badge badge-warning" style="font-size: 0.6875rem;"><span class="material-symbols-rounded" style="font-size: 11px;">warning</span> Partial</span>`;
  } else {
    statusBadge = `<span class="badge badge-danger" style="font-size: 0.6875rem;"><span class="material-symbols-rounded" style="font-size: 11px;">error</span> Failed</span>`;
  }

  const appliedPatches = att.applied_patches || [];
  const failedPatches = att.failed_patches || [];
  const repos = att.repos || [];

  const appliedHtml = appliedPatches.map(p => {
    const pName = resolvePatchDisplayName(p, '');
    const pOwner = resolvePatchOwner(p, patchOwnersMap);
    const ownerChip = formatPatchOwnerChip(pOwner);
    const optsHtml = typeof p === 'object' && p.options ? formatPatchOptions(p.options) : '';
    return `
      <div class="drawer-patch-item">
        <div class="drawer-patch-head">
          <span class="material-symbols-rounded" style="font-size: 13px; color: var(--color-success); flex-shrink: 0;">check</span>
          <span>${escapeHtml(pName)}</span>
          ${ownerChip}
          ${optsHtml}
        </div>
      </div>
    `;
  }).join('');

  let reposHtml = '';
  if (repos.length > 0) {
    reposHtml = `<div class="attempt-repos-meta"><span class="material-symbols-rounded" style="font-size: 12px;">source</span> Repos: ${repos.map(r => escapeHtml(cleanRepoSlug(r))).join(', ')}</div>`;
  }

  let failureBoxHtml = '';
  if (status !== 'success') {
    const rawReason = att.reason || (failedPatches.length ? failedPatches.map(p => typeof p === 'object' ? (p.reason || p.name) : p).join('; ') : 'Patching failed');
    const err = cleanPatchErrorMessage(rawReason);
    failureBoxHtml = `
      <div class="diag-reason" style="margin-top: 4px; font-size: 0.75rem; color: #fca5a5; background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 4px; padding: 6px 8px;">
        <div style="display: flex; align-items: flex-start; gap: 4px;">
          <span class="material-symbols-rounded" style="font-size: 14px; color: var(--color-danger); flex-shrink: 0; margin-top: 1px;">close</span>
          <span style="font-weight: 500;">${escapeHtml(err.summary || rawReason)}</span>
        </div>
        ${err.stack ? `<details class="stacktrace-details" style="margin-top: 4px;"><summary style="font-size: 0.6875rem; color: var(--text-dim); cursor: pointer;">View stack trace ▾</summary><pre class="stacktrace-pre" style="font-size: 0.6875rem; margin-top: 4px;">${escapeHtml(err.stack)}</pre></details>` : ''}
      </div>
    `;
  }

  return `
    <div class="drawer-attempt-card attempt-${status}">
      <div class="drawer-attempt-head">
        <div class="attempt-title-row">
          <span class="attempt-num">Attempt ${attNum}${totalAttempts > 1 ? ` of ${totalAttempts}` : ''}</span>
          <span class="attempt-ver">v${escapeHtml(ver)}</span>
          <span class="attempt-strat" title="${escapeHtml(stratName)}">${escapeHtml(stratName)}</span>
          ${stage !== 'Morphe Patching' ? `<span class="attempt-strat" style="opacity: 0.85;">${escapeHtml(stage)}</span>` : ''}
        </div>
        <div>${statusBadge}</div>
      </div>
      ${reposHtml}
      ${failureBoxHtml}
      ${appliedHtml ? `<div class="drawer-patch-list" style="margin-top: 4px; margin-bottom: 4px;">${appliedHtml}</div>` : ''}
    </div>
  `;
}

function formatPatchOptions(options) {
  if (!options || !Array.isArray(options) || options.length === 0) return '';
  const count = options.length;
  const fullText = options.map(o => `${o.key}: ${o.value}`).join(', ');
  if (count <= 2 && fullText.length < 35) {
    return `<span class="drawer-patch-opt" title="${escapeHtml(fullText)}">${escapeHtml(fullText)}</span>`;
  }
  return `<span class="drawer-patch-opt" title="${escapeHtml(fullText)}">${count} options</span>`;
}

function renderDrawerReposSection(repos) {
  const cleanRepos = Array.isArray(repos) ? repos : [];
  if (cleanRepos.length === 0) {
    return `
      <div class="drawer-section-title">Configured Repositories (0)</div>
      <div class="drawer-repo-list">
        <div style="font-size: 0.8125rem; color: var(--text-muted); padding: 4px 0;">No patch repositories configured for this application.</div>
      </div>
    `;
  }
  const items = cleanRepos.map(r => {
    const url = formatRepoUrl(r);
    return `
      <div class="drawer-repo-item">
        <span class="material-symbols-rounded" style="font-size: 13px; color: var(--primary-cyan);">source</span>
        <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="drawer-repo-link" title="${escapeHtml(url)}" onclick="event.stopPropagation()">
          <span>${escapeHtml(cleanRepoSlug(r))}</span>
          <span class="material-symbols-rounded" style="font-size: 11px; opacity: 0.7;">open_in_new</span>
        </a>
      </div>
    `;
  });

  return `
    <div class="drawer-section-title">Configured Repositories (${cleanRepos.length})</div>
    <div class="drawer-repo-list">
      ${items.join('')}
    </div>
  `;
}

function renderConfiguredApps() {
  const container = document.getElementById('apps-container');
  if (!container) return;

  // Preserve any currently opened drawer IDs before re-rendering
  const openDrawers = new Set(
    Array.from(container.querySelectorAll('.patch-drawer.open'))
      .map(el => el.id.replace('drawer-', ''))
      .filter(Boolean)
  );

  const totalCount = state.configuredApps.length;
  const activeCount = state.configuredApps.filter(a => a.enabled).length;
  const disabledCount = state.configuredApps.filter(a => !a.enabled).length;

  const countAllEl = document.getElementById('count-all');
  const countActiveEl = document.getElementById('count-active');
  const countDisabledEl = document.getElementById('count-disabled');
  if (countAllEl) countAllEl.textContent = totalCount;
  if (countActiveEl) countActiveEl.textContent = activeCount;
  if (countDisabledEl) countDisabledEl.textContent = disabledCount;

  const apps = state.configuredApps.filter(app => {
    // 1. App filter tab (all / active / disabled)
    if (state.appFilter === 'active' && !app.enabled) return false;
    if (state.appFilter === 'disabled' && app.enabled) return false;

    // 2. Search query filter
    if (!state.activeSearch) return true;
    const pkg = (app.package || app.package_name || '').toLowerCase();
    const catalogItem = (morpheCatalogData && morpheCatalogData[app.package || app.package_name]) ? morpheCatalogData[app.package || app.package_name] : null;
    const name = (catalogItem?.name || getFallbackAppName(app.package || app.package_name)).toLowerCase();
    return pkg.includes(state.activeSearch) || name.includes(state.activeSearch);
  });

  if (apps.length === 0) {
    let emptyMsg = 'No applications configured in config.yaml yet.';
    if (state.activeSearch) {
      emptyMsg = 'No apps match your search filter.';
    } else if (state.appFilter === 'active') {
      emptyMsg = 'No apps currently active for next run.';
    } else if (state.appFilter === 'disabled') {
      emptyMsg = 'No apps currently disabled.';
    }

    container.innerHTML = `
      <div style="grid-column: 1/-1; text-align: center; padding: 60px 20px; background: var(--bg-surface); border: 1px dashed var(--border-subtle); border-radius: var(--radius-lg);">
        <span class="material-symbols-rounded" style="font-size: 48px; color: var(--text-dim);">search_off</span>
        <h3 style="margin-top: 12px; font-family: var(--font-display);">No Applications Found</h3>
        <p style="color: var(--text-muted); font-size: 0.875rem; margin-top: 4px;">${emptyMsg}</p>
        <button onclick="openAddPackageWizard()" class="btn btn-primary btn-sm" style="margin-top: 16px;">
          <span class="material-symbols-rounded">add</span>
          <span>Add Application</span>
        </button>
      </div>
    `;
    return;
  }

  container.innerHTML = apps.map(app => {
    const pkg = app.package || app.package_name;
    const isEnabled = app.enabled !== false;
    const repos = app.repos || [];
    const aliases = app.patch_aliases || [];
    const lastRes = app.last_result;

    // Official app name & icon from Morphe catalog (or intelligent fallback)
    const catalogItem = (morpheCatalogData && morpheCatalogData[pkg]) ? morpheCatalogData[pkg] : null;
    const friendlyName = catalogItem?.name || getFallbackAppName(pkg);
    const iconUrl = catalogItem?.icon_url;

    const avatarHtml = iconUrl
      ? `<img src="${iconUrl}" alt="${escapeHtml(friendlyName)}" onerror="this.onerror=null; this.parentElement.innerHTML='<span class=\\'material-symbols-rounded\\' style=\\'font-size: 26px;\\'>android</span>'">`
      : `<span class="material-symbols-rounded" style="font-size: 26px;">android</span>`;

    // 1. Clean Front Summary Chips for Repositories (Clickable links to repositories)
    let repoSummaryChip = '';
    if (repos.length === 0) {
      repoSummaryChip = `<span class="meta-chip" title="Uses no configured patch repositories"><span class="material-symbols-rounded" style="font-size: 13px;">public_off</span> No Repos</span>`;
    } else if (repos.length <= 2) {
      repoSummaryChip = repos.map(r => {
        const rUrl = formatRepoUrl(r);
        return `<a href="${escapeHtml(rUrl)}" target="_blank" rel="noopener noreferrer" class="meta-chip repo clickable" title="Open repository: ${escapeHtml(rUrl)}" onclick="event.stopPropagation()"><span class="material-symbols-rounded" style="font-size: 13px;">source</span> ${escapeHtml(cleanRepoSlug(r))}</a>`;
      }).join('');
    } else {
      const firstTwo = repos.slice(0, 2).map(r => {
        const rUrl = formatRepoUrl(r);
        return `<a href="${escapeHtml(rUrl)}" target="_blank" rel="noopener noreferrer" class="meta-chip repo clickable" title="Open repository: ${escapeHtml(rUrl)}" onclick="event.stopPropagation()"><span class="material-symbols-rounded" style="font-size: 13px;">source</span> ${escapeHtml(cleanRepoSlug(r))}</a>`;
      }).join('');
      repoSummaryChip = `${firstTwo}<span class="meta-chip clickable" style="cursor: pointer;" title="View all ${repos.length} repositories in details drawer" onclick="event.stopPropagation(); togglePatchDrawer('${pkg}')">+${repos.length - 2} more</span>`;
    }

    const aliasChip = aliases.length > 0
      ? `<span class="meta-chip alias" title="Configured patch aliases"><span class="material-symbols-rounded" style="font-size: 13px;">alt_route</span> ${aliases.length} Aliases</span>`
      : '';

    // 2. Status Row & Drawer Content
    let statusRowHtml = '';
    let drawerHtml = '';

    if (lastRes) {
      const attemptsList = lastRes.attempts || [];
      const hasMultiAttempts = attemptsList.length > 1 || (lastRes.total_attempts || 0) > 1;
      let attemptBadgeHtml = '';
      if (hasMultiAttempts) {
        const total = lastRes.total_attempts || attemptsList.length;
        const stratWon = lastRes.winning_strategy || (attemptsList.find(a => a.status === 'success')?.strategy) || null;
        const stratText = stratWon ? ` • Strategy ${stratWon}` : '';
        attemptBadgeHtml = `<span class="badge-attempt-count" title="${total} attempts recorded across strategies for this package"><span class="material-symbols-rounded" style="font-size: 11px;">layers</span> ${total} Attempts${stratText}</span>`;
      }

      if (lastRes.is_pipeline_failure) {
        // Pipeline failed before reaching patching (e.g. Download, APKPure, Options)
        statusRowHtml = `
          <div class="app-status-row" onclick="togglePatchDrawer('${pkg}')" title="Click to view failure diagnostics">
            <div class="app-status-badges">
              <span class="badge-version">v${escapeHtml(lastRes.version || 'target')}</span>
              <span class="badge badge-danger" style="font-size: 0.75rem;"><span class="material-symbols-rounded" style="font-size: 13px;">error</span> ${escapeHtml(lastRes.failure_stage || 'Pipeline Failed')}</span>
              ${attemptBadgeHtml}
            </div>
            <button class="patch-count-btn" id="drawer-btn-${pkg}" onclick="event.stopPropagation(); togglePatchDrawer('${pkg}')">
              <span class="count-failed">Diagnostics ▾</span>
            </button>
          </div>
        `;

        let attemptsDetailHtml = '';
        if (hasMultiAttempts) {
          attemptsDetailHtml = `
            <div class="drawer-section-title">Failed Attempts (${attemptsList.length})</div>
            <div class="drawer-attempts-container">
              ${attemptsList.map((att, idx) => renderAttemptCard(att, idx, attemptsList.length, lastRes.patch_owners)).join('')}
            </div>
          `;
        } else {
          const attemptsHtml = attemptsList.map(att => {
            const v = att.version || att.target_version || 'target';
            const st = att.stage || 'Pipeline';
            const r = att.reason || 'Failed';
            return `<div class="diag-attempt-item"><span style="color: var(--primary-cyan); font-weight: 600;">v${escapeHtml(v)}</span> <span>${escapeHtml(st)}:</span> <span style="color: #fca5a5;">${escapeHtml(r)}</span></div>`;
          }).join('');

          attemptsDetailHtml = `
            <div class="diag-box">
              <div class="diag-title">
                <span class="material-symbols-rounded" style="font-size: 14px;">report_problem</span>
                <span>Pipeline Failure Diagnostics</span>
              </div>
              <div class="diag-reason">${escapeHtml(lastRes.failure_reason || 'Unknown error occurred in pipeline')}</div>
              ${attemptsHtml ? `<div class="diag-attempts">${attemptsHtml}</div>` : ''}
            </div>
          `;
        }

        drawerHtml = `
          <div class="patch-drawer" id="drawer-${pkg}">
            ${attemptsDetailHtml}

            ${renderDrawerReposSection(repos)}

            <div class="drawer-footer-time" onclick="togglePatchDrawer('${pkg}')" style="cursor: pointer; display: flex; align-items: center; justify-content: space-between;" title="Click to collapse drawer">
              <div style="display: flex; align-items: center; gap: 4px;">
                <span class="material-symbols-rounded" style="font-size: 13px;">schedule</span>
                <span>Attempted: ${lastRes.timestamp_formatted || 'Recent run'}</span>
              </div>
              <span style="color: var(--primary-cyan); font-weight: 500; font-size: 0.6875rem;">Collapse ▴</span>
            </div>
          </div>
        `;
      } else {
        // Normal patch result from result.json
        let statusBadge = '';
        if (lastRes.status === 'success') {
          statusBadge = `<span class="badge badge-success" style="font-size: 0.75rem;"><span class="material-symbols-rounded" style="font-size: 13px;">check_circle</span> Built</span>`;
        } else if (lastRes.status === 'partial') {
          statusBadge = `<span class="badge badge-warning" style="font-size: 0.75rem;"><span class="material-symbols-rounded" style="font-size: 13px;">warning</span> Partial</span>`;
        } else {
          statusBadge = `<span class="badge badge-danger" style="font-size: 0.75rem;"><span class="material-symbols-rounded" style="font-size: 13px;">error</span> Failed</span>`;
        }

        statusRowHtml = `
          <div class="app-status-row" onclick="togglePatchDrawer('${pkg}')" title="Click to view patch breakdown">
            <div class="app-status-badges">
              <span class="badge-version" title="Built target version">v${escapeHtml(lastRes.version || 'latest')}</span>
              ${statusBadge}
              ${attemptBadgeHtml}
            </div>
            <button class="patch-count-btn" id="drawer-btn-${pkg}" onclick="event.stopPropagation(); togglePatchDrawer('${pkg}')">
              <span class="count-applied">✓ ${lastRes.applied_count || 0}</span>
              ${lastRes.failed_count > 0 ? `<span class="count-failed">✗ ${lastRes.failed_count}</span>` : ''}
              <span class="material-symbols-rounded chevron">expand_more</span>
            </button>
          </div>
        `;

        let runsBodyHtml = '';
        if (hasMultiAttempts) {
          const stratWon = lastRes.winning_strategy || (attemptsList.find(a => a.status === 'success')?.strategy) || null;
          const stratSub = stratWon ? ` • Strategy ${stratWon} Succeeded` : '';
          runsBodyHtml = `
            <div class="drawer-section-title">Patch Attempts (${attemptsList.length} Attempts${stratSub})</div>
            <div class="drawer-attempts-container">
              ${attemptsList.map((att, idx) => renderAttemptCard(att, idx, attemptsList.length, lastRes.patch_owners)).join('')}
            </div>
          `;
        } else {
          // Applied Patches (with owner badge and compact options)
          const appliedList = (lastRes.applied_patches || []).map(p => {
            const pName = resolvePatchDisplayName(p, '');
            const pOwner = resolvePatchOwner(p, lastRes.patch_owners);
            const ownerChip = formatPatchOwnerChip(pOwner);
            const optsHtml = typeof p === 'object' && p.options ? formatPatchOptions(p.options) : '';
            return `
              <div class="drawer-patch-item">
                <div class="drawer-patch-head">
                  <span class="material-symbols-rounded" style="font-size: 14px; color: var(--color-success); flex-shrink: 0;">check</span>
                  <span>${escapeHtml(pName)}</span>
                  ${ownerChip}
                  ${optsHtml}
                </div>
              </div>
            `;
          }).join('');

          // Failed Patches (with owner badge, clean 1-line summary + collapsible stack trace)
          const failedList = (lastRes.failed_patches || []).map(p => {
            const pReason = typeof p === 'object' ? (p.reason || p.error || '') : '';
            const pName = resolvePatchDisplayName(p, pReason);
            const pOwner = resolvePatchOwner(p, lastRes.patch_owners);
            const ownerChip = formatPatchOwnerChip(pOwner);
            const err = cleanPatchErrorMessage(pReason);
            const stackHtml = err.stack
              ? `<details class="stacktrace-details"><summary>View stack trace ▾</summary><pre class="stacktrace-pre">${escapeHtml(err.stack)}</pre></details>`
              : '';

            return `
              <div class="drawer-patch-item failed">
                <div class="drawer-patch-head">
                  <span class="material-symbols-rounded" style="font-size: 14px; color: var(--color-danger); flex-shrink: 0;">close</span>
                  <span>${escapeHtml(pName)} ${ownerChip} — <span style="font-weight: 500;">${escapeHtml(err.summary)}</span></span>
                </div>
                ${stackHtml}
              </div>
            `;
          }).join('');

          runsBodyHtml = `
            ${appliedList ? `<div class="drawer-section-title">Applied Patches (${lastRes.applied_count})</div><div class="drawer-patch-list">${appliedList}</div>` : ''}
            ${failedList ? `<div class="drawer-section-title" style="color: var(--color-danger);">Failed Patches (${lastRes.failed_count})</div><div class="drawer-patch-list">${failedList}</div>` : ''}
            ${!appliedList && !failedList ? `<div style="color: var(--text-dim); margin-bottom: 8px;">No patch breakdown recorded in result.json.</div>` : ''}
          `;
        }

        drawerHtml = `
          <div class="patch-drawer" id="drawer-${pkg}">
            ${runsBodyHtml}

            ${renderDrawerReposSection(repos)}

            <div class="drawer-footer-time" onclick="togglePatchDrawer('${pkg}')" style="cursor: pointer; display: flex; align-items: center; justify-content: space-between;" title="Click to collapse drawer">
              <div style="display: flex; align-items: center; gap: 4px;">
                <span class="material-symbols-rounded" style="font-size: 13px;">schedule</span>
                <span>Last built: ${lastRes.timestamp_formatted || 'N/A'}</span>
              </div>
              <span style="color: var(--primary-cyan); font-weight: 500; font-size: 0.6875rem;">Collapse ▴</span>
            </div>
          </div>
        `;
      }
    } else {
      statusRowHtml = `
        <div class="app-status-row" style="color: var(--text-dim);" onclick="togglePatchDrawer('${pkg}')">
          <div class="app-status-badges">
            <span class="meta-chip" style="font-size: 0.75rem;"><span class="material-symbols-rounded" style="font-size: 13px;">hourglass_empty</span> Never Run</span>
          </div>
          <button class="patch-count-btn" id="drawer-btn-${pkg}" onclick="event.stopPropagation(); togglePatchDrawer('${pkg}')">
            <span>Repositories ▾</span>
          </button>
        </div>
      `;

      drawerHtml = `
        <div class="patch-drawer" id="drawer-${pkg}">
          ${renderDrawerReposSection(repos)}
          <div class="drawer-footer-time" onclick="togglePatchDrawer('${pkg}')" style="cursor: pointer; display: flex; align-items: center; justify-content: space-between;" title="Click to collapse drawer">
            <div style="display: flex; align-items: center; gap: 4px;">
              <span class="material-symbols-rounded" style="font-size: 13px;">info</span>
              <span>Waiting for initial patching execution.</span>
            </div>
            <span style="color: var(--primary-cyan); font-weight: 500; font-size: 0.6875rem;">Collapse ▴</span>
          </div>
        </div>
      `;
    }

    return `
      <div class="app-card ${isEnabled ? '' : 'is-disabled'}">
        <div class="app-card-header">
          <div class="app-avatar">
            ${avatarHtml}
          </div>
          <div class="app-titles">
            <div class="app-name">${escapeHtml(friendlyName)}</div>
            <div class="app-package" title="${pkg}">${pkg}</div>
          </div>
          <label class="toggle-switch" title="${isEnabled ? 'Active for next run (Click to disable)' : 'Disabled for next run (Click to enable)'}">
            <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleAppActive('${pkg}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
        </div>

        ${statusRowHtml}
        ${drawerHtml}

        <div class="app-meta-tags">
          ${repoSummaryChip}
          ${aliasChip}
        </div>

        <div class="app-card-footer">
          <button onclick="runSingleAppPatch('${pkg}')" class="btn btn-sm btn-patch-now" title="Patch only ${pkg} right now">
            <span class="material-symbols-rounded" style="font-size: 15px;">play_arrow</span>
            <span>Patch Now</span>
          </button>
          <div class="card-actions-group">
            ${lastRes?.download_url ? `
              <a href="${escapeHtml(lastRes.download_url)}" download="${escapeHtml(lastRes.apk_name || (pkg + '.apk'))}" class="btn btn-primary btn-sm" title="Direct download patched APK (v${escapeHtml(lastRes.version || '')})" onclick="event.stopPropagation()">
                <span class="material-symbols-rounded" style="font-size: 16px;">download</span>
              </a>
            ` : ''}
            <button onclick="navigateToPackageFiles('${pkg}')" class="btn btn-secondary btn-sm" title="Browse files for ${pkg}">
              <span class="material-symbols-rounded" style="font-size: 16px;">folder_open</span>
            </button>
            <button onclick="openEditAppModal('${pkg}')" class="btn btn-secondary btn-sm" title="Configure App & Patches">
              <span class="material-symbols-rounded" style="font-size: 16px;">tune</span>
            </button>
            <button onclick="deleteConfigApp('${pkg}')" class="btn btn-danger btn-sm btn-icon-only" title="Remove from config.yaml">
              <span class="material-symbols-rounded" style="font-size: 16px;">delete</span>
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');

  // Restore open state for drawers that were open prior to re-render
  openDrawers.forEach(pkg => {
    const drawer = document.getElementById('drawer-' + pkg);
    const btn = document.getElementById('drawer-btn-' + pkg);
    if (drawer) drawer.classList.add('open');
    if (btn) btn.classList.add('open');
  });
}

async function toggleAppActive(pkg, isChecked) {
  try {
    const res = await fetch('/api/config/app/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ package: pkg, enabled: isChecked })
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || 'Toggle failed');
    }
    showToast(`${pkg} ${isChecked ? 'enabled' : 'disabled'} for next run`, 'success');
    await loadConfig();
  } catch (err) {
    showToast(`Failed to toggle ${pkg}: ${err.message}`, 'error');
    await loadConfig();
  }
}

async function runSingleAppPatch(pkg) {
  try {
    const res = await fetch(`/api/run/${encodeURIComponent(pkg)}`, { method: 'POST' });
    if (res.status === 409) {
      showToast('A pipeline run is already in progress! Enabled app for future runs.', 'warning');
      openTerminalAndConnect();
      await loadConfig();
      return;
    }
    if (!res.ok) throw new Error(`Run trigger returned ${res.status}`);

    const data = await res.json().catch(() => ({}));
    const appName = getAppDisplayName(pkg);
    if (data && data.uncommented) {
      showToast(`Enabled ${appName || pkg} in config & initiated patching pipeline!`, 'success');
    } else {
      showToast(`Patching pipeline initiated for ${appName || pkg}!`, 'success');
    }
    state.status = {
      ...state.status,
      is_running: true,
      active_run: { source: 'manual', package: pkg },
      active_lock: { source: 'manual', package: pkg },
    };
    renderStatus(state.status);
    openTerminalAndConnect();
    loadStatus();
    await loadConfig();
  } catch (e) {
    showToast(`Failed to trigger patch for ${pkg}: ${e.message}`, 'error');
  }
}

function togglePatchDrawer(pkg) {
  if (!pkg) return;
  const drawer = document.getElementById('drawer-' + pkg);
  const btn = document.getElementById('drawer-btn-' + pkg);
  if (!drawer) return;
  const isCurrentlyOpen = drawer.classList.contains('open');
  if (isCurrentlyOpen) {
    drawer.classList.remove('open');
    if (btn) btn.classList.remove('open');
  } else {
    drawer.classList.add('open');
    if (btn) btn.classList.add('open');
  }
}

function cleanRepoSlug(url) {
  if (!url) return '';
  return String(url)
    .trim()
    .replace(/^https?:\/\/(www\.)?github\.com\//, '')
    .replace(/^https?:\/\/(www\.)?gitlab\.com\//, '')
    .replace(/^git@github\.com:/, '')
    .replace(/^git@gitlab\.com:/, '')
    .replace(/\.git$/, '')
    .replace(/\/+$/, '');
}

function formatRepoUrl(arg1, arg2) {
  let source = 'github';
  let repo = '';

  if (arg2 !== undefined) {
    const a1 = String(arg1).toLowerCase().trim();
    const a2 = String(arg2).toLowerCase().trim();
    if (a1 === 'github' || a1 === 'gitlab') {
      source = a1;
      repo = arg2;
    } else if (a2 === 'github' || a2 === 'gitlab') {
      source = a2;
      repo = arg1;
    } else {
      source = a1 || 'github';
      repo = arg2;
    }
  } else {
    repo = arg1;
  }

  if (!repo) return '';
  const trimmed = String(repo).trim();

  // If already a full URL
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return trimmed.replace(/\.git$/, '').replace(/\/+$/, '');
  }

  const slug = cleanRepoSlug(trimmed);
  if (source === 'gitlab' || trimmed.includes('gitlab.com')) {
    return `https://gitlab.com/${slug}`;
  }
  return `https://github.com/${slug}`;
}

function extractRepoOwner(repoUrl) {
  if (!repoUrl) return 'default';
  const clean = repoUrl.trim().replace(/\/+$/, '');
  if (clean.includes('github.com/')) {
    const parts = clean.split('github.com/')[1].split('/');
    return parts[0] || 'default';
  }
  if (clean.includes('gitlab.com/')) {
    const parts = clean.split('gitlab.com/')[1].split('/');
    return parts[0] || 'default';
  }
  const parts = clean.split('/').filter(Boolean);
  if (parts.length >= 2) return parts[parts.length - 2];
  return parts[0] || 'default';
}

// ---------------------------------------------------------------------------
// Global Patch Repositories View
// ---------------------------------------------------------------------------
function renderGlobalRepos() {
  const container = document.getElementById('repos-container');
  if (!container) return;

  const repos = state.globalRepos;
  if (repos.length === 0) {
    container.innerHTML = `<p style="color: var(--text-muted); padding: 20px;">No global patch repositories configured.</p>`;
    return;
  }

  container.innerHTML = repos.map(repo => {
    const slug = cleanRepoSlug(repo);
    const isGitLab = repo.includes('gitlab.com');
    const repoUrl = formatRepoUrl(isGitLab ? 'gitlab' : 'github', repo);

    return `
      <div class="stat-card" style="padding: 16px; align-items: center;">
        <div class="stat-icon-wrap" style="width: 38px; height: 38px;">
          <span class="material-symbols-rounded">${isGitLab ? 'code' : 'hub'}</span>
        </div>
        <div class="stat-info" style="min-width: 0;">
          <a href="${escapeHtml(repoUrl)}" target="_blank" rel="noopener noreferrer" style="font-weight: 600; font-size: 0.9375rem; color: var(--text-main); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 4px;" title="${escapeHtml(repoUrl)}" onclick="event.stopPropagation()">
            <span>${escapeHtml(slug)}</span>
            <span class="material-symbols-rounded" style="font-size: 14px; opacity: 0.7;">open_in_new</span>
          </a>
          <a href="${escapeHtml(repoUrl)}" target="_blank" rel="noopener noreferrer" style="font-size: 0.75rem; color: var(--text-dim); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block;" title="${escapeHtml(repoUrl)}" onclick="event.stopPropagation()">${escapeHtml(repo)}</a>
        </div>
        <button onclick="deleteGlobalRepo('${escapeHtml(repo)}')" class="btn btn-danger btn-sm btn-icon-only" title="Delete repository">
          <span class="material-symbols-rounded" style="font-size: 16px;">delete</span>
        </button>
      </div>
    `;
  }).join('');
}

async function promptAddGlobalRepo() {
  const url = prompt('Enter the full GitHub or GitLab repository URL:');
  if (!url || !url.trim()) return;

  try {
    const res = await fetch('/api/config/repos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: url.trim() })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to add repo');
    }
    showToast('Repository added to config.yaml', 'success');
    loadConfig();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function deleteGlobalRepo(repoUrl) {
  if (!confirm(`Are you sure you want to remove repository:\n${repoUrl}?`)) return;

  try {
    const res = await fetch('/api/config/repos', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: repoUrl })
    });
    if (!res.ok) throw new Error('Failed to delete repo');
    showToast('Repository removed', 'success');
    loadConfig();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Settings Tab & Configuration Management
// ---------------------------------------------------------------------------

async function loadSettings(showFeedback = false) {
  const btnReload = document.getElementById('btn-reload-settings');
  if (showFeedback && btnReload) {
    btnReload.disabled = true;
    btnReload.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Reloading...</span>';
  }

  try {
    const res = await fetch(`/api/settings?_t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch settings');
    const data = await res.json();
    state.settings = data;
    renderSettings(data);
    if (showFeedback) {
      showToast('Settings reloaded from disk', 'success');
    }
  } catch (err) {
    console.error('Error loading settings:', err);
    if (showFeedback) {
      showToast('Error loading settings: ' + err.message, 'error');
    }
  } finally {
    if (showFeedback && btnReload) {
      btnReload.disabled = false;
      btnReload.innerHTML = '<span class="material-symbols-rounded">refresh</span> <span>Reload</span>';
    }
  }
}

function renderSettings(settings) {
  if (!settings) return;

  const sys = settings.system || settings || {};
  const creds = settings.credentials || {};
  const email = settings.email || {};

  // 1. System & Build Configuration
  const selArch = document.getElementById('setting-arch');
  if (selArch && sys.architecture) selArch.value = sys.architecture;

  const inpTz = document.getElementById('setting-timezone');
  if (inpTz && sys.timezone !== undefined) inpTz.value = sys.timezone || 'UTC';

  const inpSched = document.getElementById('setting-schedule-time');
  if (inpSched && sys.schedule_time !== undefined) inpSched.value = sys.schedule_time || '03:00';

  const chkStrip = document.getElementById('setting-striplibs');
  if (chkStrip && sys.striplibs !== undefined) chkStrip.checked = sys.striplibs !== false;

  const chkLowSpec = document.getElementById('setting-lowspec');
  if (chkLowSpec && sys.low_spec_mode !== undefined) {
    chkLowSpec.checked = sys.low_spec_mode === true || String(sys.low_spec_mode).toLowerCase() === 'true';
  }

  const chkStartup = document.getElementById('setting-run-on-startup');
  if (chkStartup && sys.run_on_startup !== undefined) {
    chkStartup.checked = sys.run_on_startup === true || String(sys.run_on_startup).toLowerCase() === 'true';
  }

  const inpMaxLogs = document.getElementById('setting-max-logs');
  if (inpMaxLogs && sys.max_logs !== undefined && sys.max_logs !== null) {
    inpMaxLogs.value = sys.max_logs;
  }

  const chkMaxOrig = document.getElementById('setting-max-original-enable');
  const inpMaxOrig = document.getElementById('setting-max-original-packages');
  const origLimit = (sys.max_original_packages !== undefined && sys.max_original_packages !== null && sys.max_original_packages !== '' && String(sys.max_original_packages).toLowerCase() !== 'none')
    ? parseInt(sys.max_original_packages, 10)
    : null;

  if (chkMaxOrig && inpMaxOrig) {
    if (origLimit && origLimit > 0) {
      chkMaxOrig.checked = true;
      inpMaxOrig.disabled = false;
      inpMaxOrig.value = origLimit;
    } else {
      chkMaxOrig.checked = false;
      inpMaxOrig.disabled = true;
      inpMaxOrig.value = '';
    }
  }

  const chkMaxPatched = document.getElementById('setting-max-patched-enable');
  const inpMaxPatched = document.getElementById('setting-max-patched-packages');
  const patchedLimit = (sys.max_patched_packages !== undefined && sys.max_patched_packages !== null && sys.max_patched_packages !== '' && String(sys.max_patched_packages).toLowerCase() !== 'none')
    ? parseInt(sys.max_patched_packages, 10)
    : null;

  if (chkMaxPatched && inpMaxPatched) {
    if (patchedLimit && patchedLimit > 0) {
      chkMaxPatched.checked = true;
      inpMaxPatched.disabled = false;
      inpMaxPatched.value = patchedLimit;
    } else {
      chkMaxPatched.checked = false;
      inpMaxPatched.disabled = true;
      inpMaxPatched.value = '';
    }
  }

  // 2. Email Dispatcher Card
  const emailBadge = document.getElementById('email-status-badge');
  if (emailBadge) {
    emailBadge.textContent = email.enabled ? 'Enabled' : 'Disabled';
    emailBadge.className = email.enabled ? 'badge badge-success' : 'badge badge-neutral';
  }

  const elServer = document.getElementById('email-card-server');
  if (elServer) {
    const secStr = email.use_ssl ? 'SSL' : (email.use_tls ? 'STARTTLS' : 'Plain');
    elServer.textContent = `${email.smtp_server || 'smtp.gmail.com'}:${email.smtp_port || 587} (${secStr})`;
  }

  const elSender = document.getElementById('email-card-sender');
  if (elSender) {
    elSender.textContent = email.sender_email
      ? `${email.sender_name || 'AutoMorph'} <${email.sender_email}>`
      : 'Not configured';
  }

  const elRecipients = document.getElementById('email-card-recipients');
  if (elRecipients) {
    const count = (email.recipients || []).length;
    elRecipients.textContent = count === 1 ? '1 recipient' : `${count} recipients`;
  }

  const elLayout = document.getElementById('email-card-layout');
  if (elLayout) {
    const lMode = (email.layout_mode || 'auto').toUpperCase();
    elLayout.textContent = `${lMode} (threshold: ${email.compact_threshold || 15})`;
  }

  // 3. ntfy Push Notifications Card
  renderNtfySettings(settings.ntfy || {});
}

function ntfyAuthLabel(source) {
  const s = source || {};
  const conf = state.settings?.ntfy || {};
  const authMode = s.auth_mode || conf.auth_mode;
  const fromEnv = s.auth_from_env !== undefined ? s.auth_from_env : conf.auth_from_env;
  // The token itself never reaches the browser, only whether one is stored.
  const tokenSet = s.token ? true : (s.token_set !== undefined ? s.token_set : conf.token_set);
  if (tokenSet) return fromEnv ? 'Bearer token (from .env)' : 'Bearer token (stored)';
  if (s.username) return `Basic (${s.username})`;
  if (authMode === 'basic') return fromEnv ? 'Basic auth (from .env)' : 'Basic auth';
  return 'No auth';
}

function renderNtfySettings(ntfy) {
  const badge = document.getElementById('ntfy-status-badge');
  if (badge) {
    badge.textContent = ntfy.enabled ? 'Enabled' : 'Disabled';
    badge.className = ntfy.enabled ? 'badge badge-success' : 'badge badge-neutral';
  }

  // Info tiles (mirrors the Email Dispatcher card)
  const server = ntfy.server || 'https://ntfy.sh';
  const elServer = document.getElementById('ntfy-card-server');
  if (elServer) elServer.textContent = server.replace(/^https?:\/\//, '').replace(/\/+$/, '');

  const elTopic = document.getElementById('ntfy-card-topic');
  if (elTopic) elTopic.textContent = ntfy.topic ? ntfy.topic : 'Not configured';

  const elAuth = document.getElementById('ntfy-card-auth');
  if (elAuth) elAuth.textContent = ntfyAuthLabel(ntfy);

  const elDelivery = document.getElementById('ntfy-card-delivery');
  if (elDelivery) {
    const notifyOn = (ntfy.notify_on || 'always').toUpperCase();
    elDelivery.textContent = `${notifyOn} (priority ${(ntfy.priority || 'default').toLowerCase()})`;
  }

  // Wizard form fields
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) el.value = value;
  };
  setValue('ntfy-server', ntfy.server || '');
  setValue('ntfy-topic', ntfy.topic || '');
  setValue('ntfy-username', ntfy.username || '');
  // token/password are write-only: the fields stay empty and model the stored
  // secret through their placeholder + the "remove" checkboxes.
  ['ntfy-token', 'ntfy-password'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  ['ntfy-clear-token', 'ntfy-clear-password'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.checked = false;
  });
  const tokenHint = document.getElementById('ntfy-token-hint');
  if (tokenHint) {
    tokenHint.textContent = ntfy.token_set
      ? 'A token is stored; leave empty to keep it.'
      : 'No token stored yet.';
  }
  const pwHint = document.getElementById('ntfy-password-hint');
  if (pwHint) {
    pwHint.textContent = ntfy.password_set
      ? 'A password is stored; leave empty to keep it.'
      : 'No password stored yet.';
  }
  setValue('ntfy-priority', ntfy.priority || 'default');
  setValue('ntfy-notify-on', ntfy.notify_on || 'always');
  setValue('ntfy-tags', ntfy.tags || '');
  setValue('ntfy-click-url', ntfy.click_url || '');

  const chk = document.getElementById('ntfy-enabled');
  if (chk) chk.checked = !!ntfy.enabled;
}

function openNtfyModal() {
  const modal = document.getElementById('modal-ntfy');
  if (!modal) return;
  state.ntfyWizard.step = 1;
  renderNtfySettings(state.settings?.ntfy || {});
  renderNtfyWizardStep();
  setNtfyFeedback('');
  const fb = document.getElementById('ntfy-wiz-test-feedback');
  if (fb) { fb.classList.add('hidden'); fb.textContent = ''; }
  modal.classList.add('open');
}

function closeNtfyModal() {
  const modal = document.getElementById('modal-ntfy');
  if (modal) modal.classList.remove('open');
}

function renderNtfyWizardStep() {
  const step = state.ntfyWizard.step;

  [1, 2, 3].forEach((n) => {
    document.getElementById(`ntfy-wiz-step-${n}`)?.classList.toggle('hidden', n !== step);
    const ind = document.getElementById(`ntfy-wiz-ind-${n}`);
    if (ind) {
      ind.classList.toggle('active', n === step);
      ind.classList.toggle('done', n < step);
    }
  });

  const back = document.getElementById('btn-ntfy-wiz-back');
  const next = document.getElementById('btn-ntfy-wiz-next');
  const save = document.getElementById('btn-ntfy-save');
  if (back) back.disabled = step === 1;
  if (next) next.classList.toggle('hidden', step === 3);
  if (save) save.classList.toggle('hidden', step !== 3);
  if (step === 3) updateNtfyReview();
}

function ntfyWizNext() {
  if (state.ntfyWizard.step === 1) {
    const payload = ntfyFormPayload();
    if (payload.enabled && !payload.topic) {
      showToast('Please enter an ntfy topic before enabling notifications.', 'error');
      document.getElementById('ntfy-topic')?.focus();
      return;
    }
  }
  state.ntfyWizard.step = Math.min(3, state.ntfyWizard.step + 1);
  renderNtfyWizardStep();
}

function ntfyWizPrev() {
  state.ntfyWizard.step = Math.max(1, state.ntfyWizard.step - 1);
  renderNtfyWizardStep();
}

function applyNtfyPreset(url) {
  const el = document.getElementById('ntfy-server');
  if (!el) return;
  if (url === '__custom__') {
    el.value = '';
    el.focus();
    return;
  }
  el.value = url;
}

function updateNtfyReview() {
  const payload = ntfyFormPayload();

  const status = document.getElementById('ntfy-rev-status');
  if (status) {
    status.textContent = payload.enabled ? 'Enabled' : 'Disabled';
    status.style.color = payload.enabled ? '#34d399' : 'var(--text-muted)';
  }
  const serverEl = document.getElementById('ntfy-rev-server');
  if (serverEl) serverEl.textContent = payload.server || 'https://ntfy.sh';
  const topicEl = document.getElementById('ntfy-rev-topic');
  if (topicEl) topicEl.textContent = payload.topic || 'Not configured';

  const authEl = document.getElementById('ntfy-rev-auth');
  if (authEl) authEl.textContent = ntfyAuthLabel(payload);

  const optionsEl = document.getElementById('ntfy-rev-options');
  if (optionsEl) {
    const notifyOn = payload.notify_on === 'success' ? 'SUCCESS ONLY'
      : (payload.notify_on === 'failure' ? 'FAILURES ONLY' : 'EVERY RUN');
    const tags = payload.tags ? ` • tags ${payload.tags}` : '';
    optionsEl.textContent = `${notifyOn} • priority ${payload.priority}${tags}`;
  }
}

async function loadNtfyPresets() {
  const list = document.getElementById('ntfy-server-presets');
  if (!list) return;
  try {
    const res = await fetch('/api/settings/ntfy/presets');
    const data = await res.json();
    list.innerHTML = '';
    (data.presets || []).forEach((preset) => {
      const opt = document.createElement('option');
      opt.value = preset.url;
      opt.label = preset.label;
      list.appendChild(opt);
    });
  } catch (err) {
    // Presets are a convenience; a custom server can always be typed in.
  }
}

function ntfyFormPayload() {
  const payload = {
    enabled: document.getElementById('ntfy-enabled')?.checked || false,
    server: (document.getElementById('ntfy-server')?.value || '').trim(),
    topic: (document.getElementById('ntfy-topic')?.value || '').trim(),
    username: (document.getElementById('ntfy-username')?.value || '').trim(),
    clear_token: document.getElementById('ntfy-clear-token')?.checked || false,
    clear_password: document.getElementById('ntfy-clear-password')?.checked || false,
    priority: document.getElementById('ntfy-priority')?.value || 'default',
    notify_on: document.getElementById('ntfy-notify-on')?.value || 'always',
    tags: (document.getElementById('ntfy-tags')?.value || '').trim(),
    click_url: (document.getElementById('ntfy-click-url')?.value || '').trim(),
  };
  const token = (document.getElementById('ntfy-token')?.value || '').trim();
  const password = (document.getElementById('ntfy-password')?.value || '').trim();
  if (token) payload.token = token;
  if (password) payload.password = password;
  return payload;
}

function setNtfyFeedback(text, isError = false) {
  ['ntfy-feedback', 'ntfy-modal-feedback'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isError ? '#f87171' : 'var(--text-muted)';
  });
}

function setNtfyTestFeedback(text, isError = false) {
  const el = document.getElementById('ntfy-wiz-test-feedback');
  if (!el) return;
  el.classList.remove('hidden');
  el.textContent = text || '';
  el.style.background = isError ? 'rgba(248,113,113,0.12)' : 'rgba(52,211,153,0.12)';
  el.style.color = isError ? '#f87171' : '#34d399';
  el.style.border = isError ? '1px solid rgba(248,113,113,0.35)' : '1px solid rgba(52,211,153,0.35)';
}

async function saveNtfySettings() {
  const payload = ntfyFormPayload();
  if (payload.enabled && !payload.topic) {
    showToast('Please enter an ntfy topic before enabling notifications.', 'error');
    state.ntfyWizard.step = 1;
    renderNtfyWizardStep();
    document.getElementById('ntfy-topic')?.focus();
    return;
  }

  const btn = document.getElementById('btn-ntfy-save');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Saving...</span>';
  }

  try {
    const res = await fetch('/api/settings/ntfy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Could not save ntfy settings');
    const saved = data.ntfy || payload;
    if (state.settings) state.settings.ntfy = saved;
    renderNtfySettings(saved);
    if (state.ntfyWizard.step === 3) updateNtfyReview();
    closeNtfyModal();
    showToast('ntfy settings saved.', 'success');
  } catch (err) {
    showToast(`ntfy settings: ${err.message}`, 'error');
    setNtfyFeedback(err.message, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-rounded">save</span> <span>Save &amp; Apply Settings</span>';
    }
  }
}

async function sendTestNtfy(sourceId = 'btn-ntfy-test') {
  const btn = document.getElementById(sourceId);
  const originalLabel = sourceId === 'btn-ntfy-wiz-test' ? 'Send Test Notification Now' : 'Send Test Notification';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Sending...</span>';
  }
  if (sourceId === 'btn-ntfy-wiz-test') setNtfyTestFeedback('Sending test notification...');
  else setNtfyFeedback('Sending test notification...');

  try {
    const res = await fetch('/api/settings/ntfy/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ntfy_config: ntfyFormPayload() }),
    });
    const data = await res.json();
    if (!res.ok || data.success === false) throw new Error(data.error || data.detail || 'ntfy test failed');
    const msg = data.message || 'Test notification sent.';
    showToast(msg, 'success');
    if (sourceId === 'btn-ntfy-wiz-test') setNtfyTestFeedback(`✓ ${msg}`);
    else setNtfyFeedback(msg);
  } catch (err) {
    showToast(`ntfy test: ${err.message}`, 'error');
    if (sourceId === 'btn-ntfy-wiz-test') setNtfyTestFeedback(`✗ ${err.message}`, true);
    else setNtfyFeedback(err.message, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span class="material-symbols-rounded">send</span> <span>${originalLabel}</span>`;
    }
  }
}

async function saveSystemSettings() {
  const btn = document.getElementById('btn-save-system-settings');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Saving...</span>';
  }

  try {
    const maxLogsVal = parseInt(document.getElementById('setting-max-logs')?.value, 10);

    const chkMaxOrig = document.getElementById('setting-max-original-enable');
    const inpMaxOrig = document.getElementById('setting-max-original-packages');
    let maxOriginalPackages = null;
    if (chkMaxOrig?.checked) {
      const parsed = parseInt(inpMaxOrig?.value, 10);
      if (!Number.isInteger(parsed) || parsed < 1) {
        showToast('Please enter a valid number (at least 1) for Max Original Packages.', 'error');
        inpMaxOrig?.focus();
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<span class="material-symbols-rounded">save</span> <span>Save Settings</span>';
        }
        return;
      }
      maxOriginalPackages = parsed;
    }

    const chkMaxPatched = document.getElementById('setting-max-patched-enable');
    const inpMaxPatched = document.getElementById('setting-max-patched-packages');
    let maxPatchedPackages = null;
    if (chkMaxPatched?.checked) {
      const parsed = parseInt(inpMaxPatched?.value, 10);
      if (!Number.isInteger(parsed) || parsed < 1) {
        showToast('Please enter a valid number (at least 1) for Max Patched Packages.', 'error');
        inpMaxPatched?.focus();
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<span class="material-symbols-rounded">save</span> <span>Save Settings</span>';
        }
        return;
      }
      maxPatchedPackages = parsed;
    }

    const payload = {
      architecture: document.getElementById('setting-arch')?.value,
      timezone: document.getElementById('setting-timezone')?.value,
      schedule_time: document.getElementById('setting-schedule-time')?.value,
      striplibs: document.getElementById('setting-striplibs')?.checked,
      low_spec_mode: document.getElementById('setting-lowspec')?.checked,
      run_on_startup: document.getElementById('setting-run-on-startup')?.checked,
      max_logs: Number.isInteger(maxLogsVal) && maxLogsVal > 0 ? maxLogsVal : 5,
      max_original_packages: maxOriginalPackages,
      max_patched_packages: maxPatchedPackages,
    };

    const res = await fetch('/api/settings/system', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to save system settings');
    }

    showToast('System & Build settings saved successfully!', 'success');
    await loadSettings();
    await loadStatus();
  } catch (err) {
    showToast('Error saving system settings: ' + err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-rounded">save</span> <span>Save System Settings</span>';
    }
  }
}

// ---------------------------------------------------------------------------
// Email Dispatcher Configuration Wizard Modal
// ---------------------------------------------------------------------------

function openEmailWizard() {
  const modal = document.getElementById('modal-email-wizard');
  if (!modal) return;

  const email = state.settings?.email || {};

  // Initialize wizard state
  state.emailWizard.step = 1;
  state.emailWizard.recipients = Array.isArray(email.recipients) ? [...email.recipients] : [];

  // Populate inputs
  const chkEnabled = document.getElementById('email-wiz-enabled');
  if (chkEnabled) chkEnabled.checked = email.enabled !== false;

  const inpSenderName = document.getElementById('email-wiz-sender-name');
  if (inpSenderName) inpSenderName.value = email.sender_name || 'AutoMorph';

  const inpSenderEmail = document.getElementById('email-wiz-sender-email');
  if (inpSenderEmail) inpSenderEmail.value = email.sender_email || '';

  const inpSmtpServer = document.getElementById('email-wiz-smtp-server');
  if (inpSmtpServer) inpSmtpServer.value = email.smtp_server || 'smtp.gmail.com';

  const inpSmtpPort = document.getElementById('email-wiz-smtp-port');
  if (inpSmtpPort) inpSmtpPort.value = email.smtp_port || 587;

  // Security radio
  if (email.use_ssl) {
    const r = document.getElementById('sec-ssl');
    if (r) r.checked = true;
  } else if (email.use_tls) {
    const r = document.getElementById('sec-tls');
    if (r) r.checked = true;
  } else {
    const r = document.getElementById('sec-none');
    if (r) r.checked = true;
  }

  const inpPrefix = document.getElementById('email-wiz-subject-prefix');
  if (inpPrefix) inpPrefix.value = email.subject_prefix || '[AutoMorph]';

  // Layout mode radio
  const layout = (email.layout_mode || 'auto').toLowerCase();
  document.querySelectorAll('input[name="email-wiz-layout"]').forEach(r => {
    r.checked = r.value === layout;
  });

  const inpThreshold = document.getElementById('email-wiz-compact-threshold');
  if (inpThreshold) inpThreshold.value = email.compact_threshold || 15;

  const chkAttachJson = document.getElementById('email-wiz-attach-json');
  if (chkAttachJson) chkAttachJson.checked = !!email.attach_result_json;

  // Clear live test feedback banner
  const feedback = document.getElementById('email-wiz-test-feedback');
  if (feedback) {
    feedback.className = 'hidden';
    feedback.innerHTML = '';
  }

  renderEmailWizRecipients();
  updateEmailSubjectPreview();
  renderEmailWizardStep();

  modal.classList.add('open');
}

function closeEmailWizard() {
  const modal = document.getElementById('modal-email-wizard');
  if (modal) modal.classList.remove('open');
}

function renderEmailWizardStep() {
  const step = state.emailWizard.step;

  // Update step indicators
  for (let i = 1; i <= 5; i++) {
    const ind = document.getElementById(`email-wiz-ind-${i}`);
    const content = document.getElementById(`email-wiz-step-${i}`);
    if (ind) {
      ind.classList.toggle('active', i === step);
      ind.classList.toggle('completed', i < step);
    }
    if (content) {
      content.classList.toggle('hidden', i !== step);
    }
  }

  const btnBack = document.getElementById('btn-email-wiz-back');
  const btnNext = document.getElementById('btn-email-wiz-next');
  const btnSave = document.getElementById('btn-email-wiz-save');

  if (btnBack) btnBack.disabled = step === 1;

  if (step === 5) {
    if (btnNext) btnNext.classList.add('hidden');
    if (btnSave) btnSave.classList.remove('hidden');
    renderEmailWizardReview();
  } else {
    if (btnNext) btnNext.classList.remove('hidden');
    if (btnSave) btnSave.classList.add('hidden');
  }
}

function emailWizNextStep() {
  const step = state.emailWizard.step;

  // Validation
  if (step === 1) {
    const senderEmail = document.getElementById('email-wiz-sender-email')?.value.trim();
    const enabled = document.getElementById('email-wiz-enabled')?.checked;
    if (enabled && (!senderEmail || !senderEmail.includes('@'))) {
      showToast('Please enter a valid sender email address', 'warning');
      return;
    }
  } else if (step === 2) {
    const server = document.getElementById('email-wiz-smtp-server')?.value.trim();
    const port = document.getElementById('email-wiz-smtp-port')?.value.trim();
    if (!server) {
      showToast('SMTP server hostname is required', 'warning');
      return;
    }
    if (!port || isNaN(port)) {
      showToast('Valid SMTP port number is required', 'warning');
      return;
    }
  } else if (step === 3) {
    const enabled = document.getElementById('email-wiz-enabled')?.checked;
    if (enabled && state.emailWizard.recipients.length === 0) {
      // Check if user has text in input box
      const pending = document.getElementById('email-wiz-new-recipient')?.value.trim();
      if (pending && pending.includes('@')) {
        addEmailWizRecipient();
      } else {
        showToast('Please add at least one notification recipient', 'warning');
        return;
      }
    }
  }

  if (state.emailWizard.step < 5) {
    state.emailWizard.step++;
    renderEmailWizardStep();
  }
}

function emailWizPrevStep() {
  if (state.emailWizard.step > 1) {
    state.emailWizard.step--;
    renderEmailWizardStep();
  }
}

function addEmailWizRecipient() {
  const inp = document.getElementById('email-wiz-new-recipient');
  if (!inp) return;
  const val = inp.value.trim();
  if (!val) return;

  if (!val.includes('@') || !val.includes('.')) {
    showToast('Please enter a valid email address', 'warning');
    return;
  }

  if (!state.emailWizard.recipients.includes(val)) {
    state.emailWizard.recipients.push(val);
    renderEmailWizRecipients();
  }
  inp.value = '';
  inp.focus();
}

function removeEmailWizRecipient(idx) {
  state.emailWizard.recipients.splice(idx, 1);
  renderEmailWizRecipients();
}

function renderEmailWizRecipients() {
  const container = document.getElementById('email-wiz-recipients-chips');
  if (!container) return;

  if (state.emailWizard.recipients.length === 0) {
    container.innerHTML = '<span style="font-size: 0.8125rem; color: var(--text-dim); font-style: italic;">No recipients added yet.</span>';
    return;
  }

  container.innerHTML = state.emailWizard.recipients.map((rcpt, idx) => `
    <span class="badge" style="background: rgba(32, 207, 206, 0.12); color: var(--primary-cyan); border: 1px solid rgba(32, 207, 206, 0.3); padding: 4px 10px; font-size: 0.8125rem; display: inline-flex; align-items: center; gap: 6px;">
      <span class="material-symbols-rounded" style="font-size: 14px;">mail</span>
      <span>${escapeHtml(rcpt)}</span>
      <button type="button" onclick="removeEmailWizRecipient(${idx})" style="background: none; border: none; color: inherit; cursor: pointer; display: flex; align-items: center; padding: 0; margin-left: 2px;">
        <span class="material-symbols-rounded" style="font-size: 14px;">close</span>
      </button>
    </span>
  `).join('');
}

function updateEmailSubjectPreview() {
  const prefix = document.getElementById('email-wiz-subject-prefix')?.value || '[Morphe Patcher]';
  const preview = document.getElementById('email-wiz-subject-preview');
  if (preview) {
    preview.textContent = `${prefix} Build Report: 3 Patched (12:00)`;
  }
}

function applyEmailPreset(preset) {
  document.querySelectorAll('.email-preset-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.preset === preset);
    btn.style.borderColor = btn.dataset.preset === preset ? 'var(--primary-cyan)' : 'var(--border-subtle)';
  });

  const inpServer = document.getElementById('email-wiz-smtp-server');
  const inpPort = document.getElementById('email-wiz-smtp-port');
  const secTls = document.getElementById('sec-tls');
  const secSsl = document.getElementById('sec-ssl');

  if (preset === 'gmail') {
    if (inpServer) inpServer.value = 'smtp.gmail.com';
    if (inpPort) inpPort.value = 587;
    if (secTls) secTls.checked = true;
  } else if (preset === 'outlook') {
    if (inpServer) inpServer.value = 'smtp.office365.com';
    if (inpPort) inpPort.value = 587;
    if (secTls) secTls.checked = true;
  } else if (preset === 'yahoo') {
    if (inpServer) inpServer.value = 'smtp.mail.yahoo.com';
    if (inpPort) inpPort.value = 465;
    if (secSsl) secSsl.checked = true;
  } else if (preset === 'custom') {
    if (inpServer && !inpServer.value) inpServer.value = 'mail.example.com';
    if (inpPort && !inpPort.value) inpPort.value = 587;
  }
}

function getEmailWizardConfigPayload() {
  const secValue = document.querySelector('input[name="email-wiz-security"]:checked')?.value || 'tls';
  const layoutValue = document.querySelector('input[name="email-wiz-layout"]:checked')?.value || 'auto';

  return {
    enabled: document.getElementById('email-wiz-enabled')?.checked,
    sender_name: document.getElementById('email-wiz-sender-name')?.value.trim() || 'Morphe Patcher',
    sender_email: document.getElementById('email-wiz-sender-email')?.value.trim(),
    smtp_server: document.getElementById('email-wiz-smtp-server')?.value.trim(),
    smtp_port: parseInt(document.getElementById('email-wiz-smtp-port')?.value || '587', 10),
    use_tls: secValue === 'tls',
    use_ssl: secValue === 'ssl',
    recipients: [...state.emailWizard.recipients],
    subject_prefix: document.getElementById('email-wiz-subject-prefix')?.value.trim() || '[Morphe Patcher]',
    layout_mode: layoutValue,
    compact_threshold: parseInt(document.getElementById('email-wiz-compact-threshold')?.value || '15', 10),
    attach_result_json: document.getElementById('email-wiz-attach-json')?.checked,
  };
}

function renderEmailWizardReview() {
  const cfg = getEmailWizardConfigPayload();

  const elStatus = document.getElementById('email-rev-status');
  if (elStatus) {
    elStatus.textContent = cfg.enabled ? 'Enabled' : 'Disabled';
    elStatus.style.color = cfg.enabled ? 'var(--color-success)' : 'var(--text-dim)';
  }

  const elServer = document.getElementById('email-rev-server');
  if (elServer) {
    const secStr = cfg.use_ssl ? 'Direct SSL' : (cfg.use_tls ? 'STARTTLS' : 'Plain');
    elServer.textContent = `${cfg.smtp_server}:${cfg.smtp_port} (${secStr})`;
  }

  const elSender = document.getElementById('email-rev-sender');
  if (elSender) {
    elSender.textContent = `${cfg.sender_name} <${cfg.sender_email || 'none'}>`;
  }

  const elRecipients = document.getElementById('email-rev-recipients');
  if (elRecipients) {
    elRecipients.textContent = cfg.recipients.length > 0 ? cfg.recipients.join(', ') : 'No recipients';
  }

  const elLayout = document.getElementById('email-rev-layout');
  if (elLayout) {
    const jsonStr = cfg.attach_result_json ? 'Attach JSON On' : 'Attach JSON Off';
    elLayout.textContent = `${cfg.layout_mode.toUpperCase()} • ${jsonStr}`;
  }
}

async function sendTestEmailFromWizard() {
  const btn = document.getElementById('btn-email-wiz-test');
  const feedback = document.getElementById('email-wiz-test-feedback');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Sending Test...</span>';
  }

  if (feedback) {
    feedback.className = 'hidden';
    feedback.innerHTML = '';
  }

  try {
    const cfg = getEmailWizardConfigPayload();
    const res = await fetch('/api/settings/email/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email_config: cfg }),
    });

    const result = await res.json();
    if (!res.ok || !result.success) {
      throw new Error(result.error || result.detail || 'Test transmission failed');
    }

    if (feedback) {
      feedback.className = '';
      feedback.style.background = 'rgba(16, 185, 129, 0.1)';
      feedback.style.border = '1px solid rgba(16, 185, 129, 0.3)';
      feedback.style.color = '#34d399';
      feedback.innerHTML = `<span class="material-symbols-rounded" style="vertical-align: middle; margin-right: 6px;">check_circle</span> <strong>Success:</strong> ${escapeHtml(result.message || 'Test email successfully dispatched!')}`;
    }
    showToast('Test email sent successfully!', 'success');
  } catch (err) {
    if (feedback) {
      feedback.className = '';
      feedback.style.background = 'rgba(239, 68, 68, 0.1)';
      feedback.style.border = '1px solid rgba(239, 68, 68, 0.3)';
      feedback.style.color = '#f87171';
      feedback.innerHTML = `<span class="material-symbols-rounded" style="vertical-align: middle; margin-right: 6px;">error</span> <strong>Error:</strong> ${escapeHtml(err.message)}`;
    }
    showToast('Failed to send test email: ' + err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-rounded">send</span> <span>Send Test Email Now</span>';
    }
  }
}

async function sendQuickTestEmail() {
  const btn = document.getElementById('btn-email-test-quick');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Sending...</span>';
  }

  try {
    const res = await fetch('/api/settings/email/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });

    const result = await res.json();
    if (!res.ok || !result.success) {
      throw new Error(result.error || result.detail || 'Test email failed');
    }

    showToast(result.message || 'Test email dispatched successfully!', 'success');
  } catch (err) {
    showToast('Test email error: ' + err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-rounded">send</span> <span>Send Test Email</span>';
    }
  }
}

async function saveEmailWizard() {
  const btn = document.getElementById('btn-email-wiz-save');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded" style="animation: spin 0.8s linear infinite;">sync</span> <span>Saving...</span>';
  }

  try {
    const payload = getEmailWizardConfigPayload();
    const res = await fetch('/api/settings/email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to save email settings');
    }

    showToast('Email dispatcher settings saved successfully!', 'success');
    closeEmailWizard();
    await loadSettings();
  } catch (err) {
    showToast('Error saving email configuration: ' + err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-rounded">save</span> <span>Save & Apply Settings</span>';
    }
  }
}

// ---------------------------------------------------------------------------
// App Deletion
// ---------------------------------------------------------------------------
async function deleteConfigApp(pkg) {
  if (!confirm(`Remove ${pkg} from config.yaml?`)) return;

  try {
    const res = await fetch(`/api/config/app/${encodeURIComponent(pkg)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error('Deletion failed');
    showToast(`Removed ${pkg}`, 'success');
    loadConfig();
    loadStatus();
  } catch (e) {
    showToast(`Error deleting app: ${e.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// Live Terminal & SSE Log Streaming
// ---------------------------------------------------------------------------
async function triggerPipelineRun() {
  try {
    const res = await fetch('/api/run', { method: 'POST' });
    if (res.status === 409) {
      showToast('A pipeline run is already in progress!', 'warning');
      openTerminalAndConnect();
      return;
    }
    if (!res.ok) throw new Error(`Run trigger returned ${res.status}`);

    showToast('Patching pipeline initiated!', 'success');
    state.status = {
      ...state.status,
      is_running: true,
      active_run: { source: 'manual', package: null },
      active_lock: { source: 'manual', package: null },
    };
    renderStatus(state.status);
    openTerminalAndConnect();
    loadStatus();
  } catch (e) {
    showToast(`Failed to trigger pipeline: ${e.message}`, 'error');
  }
}

function openTerminalAndConnect() {
  const termOutput = document.getElementById('terminal-output');
  if (termOutput) termOutput.innerHTML = '';
  openTerminalModal();
  connectSseStream();
}

function openTerminalModal() {
  document.getElementById('modal-terminal')?.classList.add('open');
  const lockStart = state.status?.active_lock?.started_at;
  state.streamStartTime = lockStart ? (lockStart * 1000) : Date.now();
  startElapsedTimer();
}

function closeTerminalModal() {
  document.getElementById('modal-terminal')?.classList.remove('open');
  if (state.eventSource && !state.status?.is_running) {
    state.eventSource.close();
    state.eventSource = null;
  }
  clearInterval(state.streamTimerInterval);
}

function startElapsedTimer() {
  clearInterval(state.streamTimerInterval);
  const el = document.getElementById('term-time-elapsed');
  state.streamTimerInterval = setInterval(() => {
    if (!state.streamStartTime) return;
    const secs = Math.max(0, Math.floor((Date.now() - state.streamStartTime) / 1000));
    if (el) el.textContent = `Elapsed: ${formatDuration(secs)}`;
  }, 1000);
}

function connectSseStream() {
  if (state.eventSource) {
    state.eventSource.close();
  }

  const output = document.getElementById('terminal-output');
  const badge = document.getElementById('term-status-badge');
  const triggerBadge = document.getElementById('term-trigger-badge');
  const stopBtn = document.getElementById('btn-term-stop');
  setTerminalReportVisible(false);
  if (stopBtn) {
    stopBtn.style.display = 'inline-flex';
    stopBtn.disabled = false;
    stopBtn.className = 'btn btn-danger btn-sm';
    stopBtn.dataset.mode = 'stop';
    stopBtn.innerHTML = '<span class="material-symbols-rounded">stop_circle</span><span>Stop</span>';
  }
  badge.className = 'badge badge-pulse badge-info';
  badge.textContent = 'Streaming';

  if (triggerBadge) {
    const triggerInfo = getRunTriggerInfo(state.status?.active_run || state.status?.active_lock);
    triggerBadge.className = 'badge badge-warning';
    triggerBadge.textContent = triggerInfo.consoleBadgeText;
    triggerBadge.style.display = 'inline-flex';
  }

  const es = new EventSource('/api/run/stream');
  state.eventSource = es;

  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      appendTerminalLine(data.line || '');
    } catch (e) {
      appendTerminalLine(event.data);
    }
  };

  es.addEventListener('complete', (event) => {
    const stopBtn = document.getElementById('btn-term-stop');
    if (stopBtn) {
      stopBtn.style.display = 'none';
    }
    setTerminalReportVisible(true);
    const triggerBadge = document.getElementById('term-trigger-badge');
    try {
      const data = JSON.parse(event.data);
      const isSuccess = data.status === 'success';
      const isCancelled = data.returncode === 130 || data.status === 'stopped';
      badge.className = isSuccess ? 'badge badge-success' : 'badge badge-danger';
      badge.textContent = isSuccess ? 'Completed' : (isCancelled ? 'Stopped' : 'Failed');
      if (triggerBadge && (data.package || data.source)) {
        const triggerInfo = getRunTriggerInfo(data);
        triggerBadge.className = 'badge badge-warning';
        triggerBadge.textContent = triggerInfo.consoleBadgeText;
        triggerBadge.style.display = 'inline-flex';
      }
      const durStr = data.duration_formatted || formatDuration(data.duration_seconds);
      appendTerminalLine(`\n[✓] Run completed in ${durStr} (Code: ${data.returncode})`);
    } catch (e) {
      appendTerminalLine('\n[✓] Pipeline finished.');
    }
    es.close();
    state.eventSource = null;
    loadAllData();
  });

  es.onerror = () => {
    // If the server completes and closes cleanly or stream ended
    loadStatus().then(() => {
      if (!state.status?.is_running) {
        badge.className = 'badge badge-success';
        badge.textContent = 'Finished';
        const stopBtn = document.getElementById('btn-term-stop');
        if (stopBtn) {
          stopBtn.style.display = 'none';
        }
        setTerminalReportVisible(true);
      }
    });
  };
}

async function stopRunningPipeline() {
  const btn = document.getElementById('btn-term-stop');

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-rounded">hourglass_empty</span><span>Stopping...</span>';
  }

  try {
    const res = await fetch('/api/run/stop', { method: 'POST' });
    const data = await res.json().catch(() => ({}));

    if (res.ok) {
      showToast('Pipeline stopped', 'info');
      if (btn) btn.style.display = 'none';
      const badge = document.getElementById('term-status-badge');
      if (badge) {
        badge.className = 'badge badge-danger';
        badge.textContent = 'Stopped';
      }
      await loadAllData();
    } else {
      const errorMsg = data.message || (typeof data.detail === 'string' ? data.detail : (data.detail?.message || 'Failed to stop'));
      showToast(`Stop request: ${errorMsg}`, 'warning');
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span class="material-symbols-rounded">stop_circle</span><span>Stop</span>';
      }
    }
  } catch (err) {
    showToast(`Failed to stop pipeline: ${err.message}`, 'error');
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-rounded">stop_circle</span><span>Stop</span>';
    }
  }
}

function appendTerminalLine(text) {
  const output = document.getElementById('terminal-output');
  if (!output) return;

  const div = document.createElement('div');
  div.className = 'terminal-line';

  // Strip leading whitespace, newlines, and box-drawing tree characters to identify tags
  const clean = text.replace(/^[\s\r\n\u2500-\u257F\u00A0]+/, '');

  if (clean.startsWith('[*]')) {
    div.classList.add('log-info');
  } else if (clean.startsWith('[✓]') || clean.startsWith('[✔]')) {
    div.classList.add('log-success');
  } else if (clean.startsWith('[!]')) {
    div.classList.add('log-warning');
  } else if (clean.startsWith('[✗]') || clean.startsWith('[x]') || clean.startsWith('[X]')) {
    div.classList.add('log-error');
  } else if (text.includes('[✓]') || text.includes('[✔]')) {
    div.classList.add('log-success');
  } else if (text.includes('[✗]')) {
    div.classList.add('log-error');
  } else if (text.includes('[!]')) {
    div.classList.add('log-warning');
  } else if (text.includes('[*]')) {
    div.classList.add('log-info');
  }

  // Prominently highlight core patch milestones
  if (text.includes('Successfully patched')) {
    div.classList.add('log-success', 'log-patch-success');
  } else if (text.includes('Morphe CLI failed') || text.includes('All attempts failed for')) {
    div.classList.remove('log-warning');
    div.classList.add('log-error', 'log-patch-error');
  }

  div.textContent = text;
  output.appendChild(div);

  const autoScroll = document.getElementById('term-autoscroll')?.checked;
  if (autoScroll) {
    output.scrollTop = output.scrollHeight;
  }
}

function copyTerminalOutput() {
  const text = document.getElementById('terminal-output')?.innerText || '';
  navigator.clipboard.writeText(text).then(() => {
    showToast('Logs copied to clipboard');
  });
}

async function openLastRunLogModal() {
  // If pipeline is actively running, open live terminal and connect SSE
  if (state.status?.is_running) {
    openTerminalAndConnect();
    return;
  }

  openTerminalModal();
  const stopBtn = document.getElementById('btn-term-stop');
  if (stopBtn) {
    stopBtn.style.display = 'none';
  }
  setTerminalReportVisible(true);
  const triggerBadge = document.getElementById('term-trigger-badge');
  if (triggerBadge) {
    triggerBadge.style.display = 'none';
  }
  const output = document.getElementById('terminal-output');
  const badge = document.getElementById('term-status-badge');
  const elapsedEl = document.getElementById('term-time-elapsed');
  clearInterval(state.streamTimerInterval);

  if (badge) {
    badge.className = 'badge badge-pulse badge-info';
    badge.textContent = 'Loading Log...';
  }
  if (output) {
    output.innerHTML = '<div class="terminal-line log-info">[*] Fetching execution log of the last run...</div>';
  }

  try {
    const res = await fetch('/api/run/logs');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (output) output.innerHTML = '';

    const lastRun = data.run_info || state.status?.last_run;
    const isSuccess = (lastRun?.status === 'success') || (data.status === 'success');
    const isFailed = (lastRun?.status === 'failed') || (data.status === 'failed');

    if (badge) {
      if (isSuccess) {
        badge.className = 'badge badge-success';
        badge.textContent = 'Completed';
      } else if (isFailed) {
        badge.className = 'badge badge-danger';
        badge.textContent = 'Failed';
      } else if (lastRun?.status) {
        badge.className = 'badge badge-secondary';
        badge.textContent = lastRun.status;
      } else {
        badge.className = 'badge badge-secondary';
        badge.textContent = 'Log View';
      }
    }

    if (triggerBadge && lastRun && (lastRun.source || lastRun.package)) {
      const triggerInfo = getRunTriggerInfo(lastRun);
      triggerBadge.className = 'badge badge-warning';
      triggerBadge.textContent = triggerInfo.consoleBadgeText;
      triggerBadge.style.display = 'inline-flex';
    }

    if (elapsedEl && lastRun) {
      const dur = lastRun.duration_formatted || formatDuration(lastRun.duration_seconds || lastRun.duration);
      elapsedEl.textContent = `Duration: ${dur}`;
    } else if (elapsedEl) {
      elapsedEl.textContent = 'Duration: --';
    }

    const lines = data.lines || [];
    if (lines.length === 0) {
      if (lastRun) {
        appendTerminalLine(`[*] AutoMorph Last Pipeline Run (${lastRun.source || 'pipeline'})`);
        appendTerminalLine(`[*] Status: ${lastRun.status || 'unknown'}`);
        appendTerminalLine(`[*] Duration: ${lastRun.duration_formatted || formatDuration(lastRun.duration_seconds || lastRun.duration)}`);
        appendTerminalLine(`[*] No verbose console log file is currently saved on disk for this run.`);
      } else {
        appendTerminalLine('[*] No previous pipeline execution logs found.');
        appendTerminalLine('[*] Click "Run Pipeline" to trigger a build and stream live execution logs.');
      }
    } else {
      for (const line of lines) {
        appendTerminalLine(typeof line === 'string' ? line : (line.line || ''));
      }
    }
  } catch (err) {
    console.error('Failed to load last run log:', err);
    if (badge) {
      badge.className = 'badge badge-danger';
      badge.textContent = 'Error';
    }
    if (output) {
      appendTerminalLine(`[✗] Could not load last execution log: ${err.message}`);
    }
  }
}

// ---------------------------------------------------------------------------
// "Add Package" Multi-Step Wizard Logic
// ---------------------------------------------------------------------------
function openAddPackageWizard() {
  state.wizard = {
    step: 1,
    selectedPackage: '',
    selectedAppName: '',
    selectedIconUrl: '',
    isCustomPackage: false,
    suggestedRepos: [],
    repos: new Set(),
    aliases: [],
    lastLoadedPackage: '',
  };
  state.wizardPatches = {
    repoPatches: {},
    patchSearch: '',
    patchFilter: 'all',
    scopeFilter: 'all',
    selectedRepoOwner: 'all',
  };

  const aliasesList = document.getElementById('wiz-aliases-list');
  if (aliasesList) aliasesList.innerHTML = '';

  updateWizardStepView(1);
  document.getElementById('modal-wizard')?.classList.add('open');
  searchCatalog(''); // Initial empty search
}

function openWizardLoadingModal(title, subtitle) {
  const modal = document.getElementById('modal-wizard-loading');
  if (!modal) return;
  const titleEl = document.getElementById('wiz-loading-title');
  const subEl = document.getElementById('wiz-loading-subtitle');
  if (titleEl && title) titleEl.textContent = title;
  if (subEl && subtitle) subEl.textContent = subtitle;
  modal.classList.add('open');
}

function updateWizardLoadingModal(title, subtitle) {
  const titleEl = document.getElementById('wiz-loading-title');
  const subEl = document.getElementById('wiz-loading-subtitle');
  if (titleEl && title) titleEl.textContent = title;
  if (subEl && subtitle) subEl.textContent = subtitle;
}

function closeWizardLoadingModal() {
  document.getElementById('modal-wizard-loading')?.classList.remove('open');
}

function closeWizardModal() {
  closeWizardLoadingModal();
  document.getElementById('modal-wizard')?.classList.remove('open');
}

function updateWizardStepView(step) {
  state.wizard.step = step;

  // Indicators
  for (let i = 1; i <= 5; i++) {
    const ind = document.getElementById(`wiz-step-ind-${i}`);
    const content = document.getElementById(`wiz-step-${i}`);

    if (ind) {
      ind.classList.toggle('active', i === step);
      ind.classList.toggle('done', i < step);
    }
    if (content) {
      content.classList.toggle('hidden', i !== step);
    }
  }

  // Back/Next/Save buttons
  const btnBack = document.getElementById('btn-wiz-back');
  const btnNext = document.getElementById('btn-wiz-next');
  const btnSave = document.getElementById('btn-wiz-save');

  if (btnBack) btnBack.disabled = (step === 1);
  if (btnNext) btnNext.classList.toggle('hidden', step === 5);
  if (btnSave) btnSave.classList.toggle('hidden', step !== 5);

  if (step === 5) {
    generateYamlPreview();
  }
}

function wizardPrevStep() {
  if (state.wizard.step > 1) {
    updateWizardStepView(state.wizard.step - 1);
  }
}

async function wizardNextStep() {
  const step = state.wizard.step;

  // Validation per step
  if (step === 1) {
    if (state.wizard.isCustomPackage) {
      const customPkg = document.getElementById('wiz-custom-pkg-input')?.value.trim();
      if (!customPkg || !customPkg.includes('.')) {
        showToast('Please enter a valid Android package ID (e.g. com.example.app)', 'warning');
        return;
      }
      state.wizard.selectedPackage = customPkg;
      state.wizard.selectedAppName = customPkg;
    } else {
      if (!state.wizard.selectedPackage) {
        showToast('Please select an application from the catalog or enter a custom package', 'warning');
        return;
      }
    }

    // Load repos for selected package
    await loadSuggestedReposForPackage(state.wizard.selectedPackage);
    updateWizardStepView(2);

  } else if (step === 2) {
    // Step 2 -> Step 3: Explicitly sync state.wizard.repos from the checked checkboxes in the DOM
    const checkedInputs = Array.from(document.querySelectorAll('#wiz-suggested-repos-list input[type="checkbox"]:checked'));
    const checkedUrls = checkedInputs.map(input => input.value).filter(Boolean);
    if (checkedUrls.length === 0) {
      showToast('Please select at least one repository.', 'warning');
      return;
    }
    state.wizard.repos = new Set(checkedUrls);

    const btnNext = document.getElementById('btn-wiz-next');
    const originalNextHtml = btnNext ? btnNext.innerHTML : '';
    if (btnNext) {
      btnNext.disabled = true;
      btnNext.innerHTML = `<span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> <span>Preparing...</span>`;
    }

    const repoNames = checkedUrls.map(cleanRepoSlug).join(', ');
    openWizardLoadingModal(
      'Preparing Patch Options...',
      `Checking repository cache & loading patches for ${repoNames}...`
    );

    try {
      // Load patch definitions for selected repos
      await loadWizardPatches();
      updateWizardStepView(3);
    } catch (err) {
      console.error('Failed to load wizard patches:', err);
      showToast(`Error preparing patches: ${err.message || err}`, 'error');
    } finally {
      closeWizardLoadingModal();
      if (btnNext) {
        btnNext.disabled = false;
        btnNext.innerHTML = originalNextHtml;
      }
    }

  } else if (step === 3) {
    // Step 3 -> Step 4: Populate/refresh patch aliases
    refreshWizardAliases();
    updateWizardStepView(4);

  } else if (step === 4) {
    // Step 4 -> Step 5: Review preview
    updateWizardStepView(5);
  }
}

function setTerminalReportVisible(visible) {
  const reportBtn = document.getElementById('btn-term-report');
  if (reportBtn) {
    reportBtn.style.display = visible ? 'inline-flex' : 'none';
  }
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Client-Side Live Morphe Catalog Engine (Direct Browser Fetch)
// ---------------------------------------------------------------------------
const MORPHE_OFFICIAL_REPO_URL = 'https://github.com/MorpheApp/morphe-patches';
const MORPHE_DEFAULT_APPS = {
  'com.google.android.youtube': {
    package: 'com.google.android.youtube',
    name: 'YouTube',
    category: 'Video',
    description: 'Official YouTube app with ad-blocking, background playback, and customization options.',
    icon_url: 'https://play-lh.googleusercontent.com/QNmuZQc9I6Zbe3mWnSr0hycnENqGFCI5p3yE29Hkxtf22T0IWS6zTrpxULLyyjWpB7ONAXDsDQXnXcVWokl3eg=s128-rw',
    patch_count: 85,
  },
  'com.google.android.apps.youtube.music': {
    package: 'com.google.android.apps.youtube.music',
    name: 'YouTube Music',
    category: 'Music & Audio',
    description: 'Official YouTube Music streaming app with ad-free listening and background play.',
    icon_url: 'https://play-lh.googleusercontent.com/MyptZXmcCyF59phIWPsNCfHq6zSO_ojEdw_4rMYYWPOGdikSjqeOXcoszBZ053VYpINe5K7ZR9KV1nkFWgxdOw=s128-rw',
    patch_count: 25,
  },
  'com.reddit.frontpage': {
    package: 'com.reddit.frontpage',
    name: 'Reddit',
    category: 'Social & Communication',
    description: 'Discover why people are the best - through real & authentic conversations.',
    icon_url: 'https://play-lh.googleusercontent.com/8FVS0kvM60JwqZWRNzBIlwrZZxJa8vpli8IWaaTo8QXRe1PHmCNJ8UpWoXkGPV3jKEaTY2OVAeYoisOnGJXY=s128-rw',
    patch_count: 20,
  },
};

let morpheCatalogData = null;
try {
  sessionStorage.removeItem('morphe_catalog_data_v5');
  sessionStorage.removeItem('morphe_catalog_data_v6');
  const cachedCatalog = sessionStorage.getItem('morphe_catalog_data_v7');
  if (cachedCatalog) morpheCatalogData = JSON.parse(cachedCatalog);
} catch (_) {}

let isFetchingMorpheCatalog = false;

async function fetchMorpheCatalog(force = false) {
  if (force) {
    morpheCatalogData = null;
    try {
      sessionStorage.removeItem('morphe_catalog_data_v7');
    } catch (_) {}
  }
  if (morpheCatalogData) return morpheCatalogData;

  if (!force) {
    try {
      const cached = sessionStorage.getItem('morphe_catalog_data_v7');
      if (cached) {
        morpheCatalogData = JSON.parse(cached);
        return morpheCatalogData;
      }
    } catch (_) {}
  }

  if (isFetchingMorpheCatalog) {
    while (isFetchingMorpheCatalog) {
      await new Promise(r => setTimeout(r, 50));
    }
    return morpheCatalogData;
  }

  isFetchingMorpheCatalog = true;
  try {
    let res;
    try {
      res = await fetch(`/api/catalog${force ? '?refresh=true' : ''}`);
      if (!res.ok) throw new Error(`API catalog HTTP ${res.status}`);
    } catch (apiErr) {
      console.warn('Local catalog API unavailable, falling back to remote Morphe catalog:', apiErr);
      res = await fetch('https://morphe-patches.software/data/bundles.json');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();

    const store = data.store || {};
    const bundles = data.bundles || [];
    const compatibilities = data.compatibilities || [];

    const catalog = {};

    // 1. Populate default apps into catalog
    for (const [defPkg, defInfo] of Object.entries(MORPHE_DEFAULT_APPS)) {
      catalog[defPkg] = {
        package: defPkg,
        name: defInfo.name,
        category: defInfo.category,
        description: defInfo.description,
        icon_url: defInfo.icon_url,
        supported_repos: [],
        repo_details: [],
        patches_by_repo: {},
      };
    }

    // 2. Populate store entries (overriding or supplementing)
    for (const [pkg, sInfo] of Object.entries(store)) {
      if (!pkg || pkg === '__universal__') continue;
      if (!catalog[pkg]) {
        catalog[pkg] = {
          package: pkg,
          name: sInfo.name || pkg,
          category: sInfo.category || 'Other',
          description: sInfo.description || '',
          icon_url: sInfo.iconUrl || '',
          supported_repos: [],
          repo_details: [],
          patches_by_repo: {},
        };
      } else {
        if (sInfo.name && sInfo.name !== pkg) catalog[pkg].name = sInfo.name;
        if (sInfo.iconUrl && !sInfo.iconUrl.includes('6Xy_7_z21b8') && !sInfo.iconUrl.includes('s_5l5Y_P_hYlV')) {
          catalog[pkg].icon_url = sInfo.iconUrl;
        }
        if (sInfo.description) catalog[pkg].description = sInfo.description;
        if (sInfo.category) catalog[pkg].category = sInfo.category;
      }
    }

    // Ensure official default apps always maintain verified icons
    for (const [defPkg, defInfo] of Object.entries(MORPHE_DEFAULT_APPS)) {
      if (catalog[defPkg] && defInfo.icon_url) {
        if (!catalog[defPkg].icon_url || catalog[defPkg].icon_url.includes('6Xy_7_z21b8') || catalog[defPkg].icon_url.includes('s_5l5Y_P_hYlV')) {
          catalog[defPkg].icon_url = defInfo.icon_url;
        }
      }
    }

    function ensureCatalogApp(appPkg) {
      if (!appPkg || appPkg === '__universal__') return null;
      if (!catalog[appPkg]) {
        catalog[appPkg] = {
          package: appPkg,
          name: appPkg,
          category: 'Other',
          description: '',
          icon_url: '',
          supported_repos: [],
          repo_details: [],
          patches_by_repo: {},
        };
      }
      if (!catalog[appPkg].patches_by_repo) catalog[appPkg].patches_by_repo = {};
      return catalog[appPkg];
    }

    function linkRepoToApp(appPkg, rUrl, bName, src, patchCnt, isOfficial = false) {
      const app = ensureCatalogApp(appPkg);
      if (!app) return;
      const existingIdx = app.supported_repos.indexOf(rUrl);
      const detail = {
        repo_url: rUrl,
        name: bName,
        source: src,
        patch_count: patchCnt,
      };
      if (existingIdx === -1) {
        if (isOfficial || rUrl === MORPHE_OFFICIAL_REPO_URL) {
          app.supported_repos.unshift(rUrl);
          app.repo_details.unshift(detail);
        } else {
          app.supported_repos.push(rUrl);
          app.repo_details.push(detail);
        }
      } else if (isOfficial || rUrl === MORPHE_OFFICIAL_REPO_URL) {
        if (existingIdx > 0) {
          app.supported_repos.splice(existingIdx, 1);
          app.supported_repos.unshift(rUrl);
          const oldDetail = app.repo_details.splice(existingIdx, 1)[0] || detail;
          app.repo_details.unshift(oldDetail);
        }
      }
    }

    function attachPatchToApp(appPkg, rUrl, patchObj, isUniversal = false) {
      const app = ensureCatalogApp(appPkg);
      if (!app) return;
      if (!app.patches_by_repo[rUrl]) app.patches_by_repo[rUrl] = [];
      const isUniv = Boolean(isUniversal || patchObj.isUniversal === true || patchObj.scope === 'universal' || determinePatchScope(patchObj.name, patchObj) === 'universal');
      const isDefault = isUniv ? false : Boolean(patchObj.default === true || patchObj.use === true);
      if (!app.patches_by_repo[rUrl].some(p => p.name === patchObj.name)) {
        app.patches_by_repo[rUrl].push({
          name: patchObj.name,
          description: patchObj.description || '',
          default: isDefault,
          use: isDefault,
          isUniversal: isUniv,
          scope: isUniv ? 'universal' : 'app',
          options: patchObj.options || null
        });
      }
    }

    // 3. Link bundles and patches from catalog (community + official bundles)
    for (const bundle of bundles) {
      const source = bundle.source || 'github';
      const repoSlug = bundle.repo || '';
      const repoUrl = formatRepoUrl(source, repoSlug);
      if (!repoUrl) continue;

      const isMorpheOfficial = (repoSlug.toLowerCase() === 'morpheapp/morphe-patches' || repoUrl === MORPHE_OFFICIAL_REPO_URL);
      const bundleName = bundle.name || (isMorpheOfficial ? 'Morphe' : repoSlug);
      const targetApps = bundle.targetApps || [];
      const patches = bundle.patches || [];

      for (const appPkg of targetApps) {
        linkRepoToApp(appPkg, repoUrl, bundleName, source, patches.length, isMorpheOfficial);
      }

      for (const patch of patches) {
        const compKey = patch.compatiblePackagesKey ?? patch.compatibilityKey;
        if (compKey !== undefined && compKey !== null && compatibilities[compKey]) {
          const entries = compatibilities[compKey];
          let isUniversalCompat = false;
          if (Array.isArray(entries)) {
            if (entries.length === 0 || entries.some(e => (typeof e === 'string' ? e : (e?.packageName || e?.package)) === '__universal__')) {
              isUniversalCompat = true;
            }
          }
          if (isUniversalCompat) {
            if (targetApps.length > 0) {
              for (const appPkg of targetApps) {
                attachPatchToApp(appPkg, repoUrl, patch, true);
              }
            }
          } else if (Array.isArray(entries)) {
            for (const entry of entries) {
              const epkg = typeof entry === 'string' ? entry : (entry?.packageName || entry?.package);
              if (epkg && epkg !== '__universal__') {
                linkRepoToApp(epkg, repoUrl, bundleName, source, patches.length, isMorpheOfficial);
                attachPatchToApp(epkg, repoUrl, patch, false);
              }
            }
          }
        } else if (targetApps.length > 0) {
          for (const appPkg of targetApps) {
            attachPatchToApp(appPkg, repoUrl, patch, true);
          }
        }
      }
    }

    // 4. Guarantee official Morphe repository is linked at top for all default apps
    for (const [defPkg, defInfo] of Object.entries(MORPHE_DEFAULT_APPS)) {
      linkRepoToApp(defPkg, MORPHE_OFFICIAL_REPO_URL, 'Morphe', 'github', defInfo.patch_count, true);
    }

    morpheCatalogData = catalog;
    try {
      sessionStorage.setItem('morphe_catalog_data_v7', JSON.stringify(catalog));
    } catch (_) {}
    return catalog;
  } catch (err) {
    console.warn('Direct Morphe catalog load failed:', err);
    return null;
  } finally {
    isFetchingMorpheCatalog = false;
  }
}

async function searchCatalog(query) {
  const container = document.getElementById('wiz-catalog-results');
  container.innerHTML = `<div style="text-align: center; padding: 20px; color: var(--text-dim);"><span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> Loading live catalog...</div>`;

  const catalog = await fetchMorpheCatalog();
  if (!catalog) {
    container.innerHTML = `<p style="padding: 16px; color: var(--text-muted); text-align: center;">Could not load live Morphe catalog. You can enter a custom package ID above.</p>`;
    return;
  }

  const q = (query || '').toLowerCase().trim();
  const allApps = Object.values(catalog);

  const DEFAULT_PRIORITY_PKGS = [
    'com.google.android.youtube',
    'com.google.android.apps.youtube.music',
    'com.reddit.frontpage'
  ];

  const items = allApps.filter(item => {
    if (!q) return true;
    const pkgLower = item.package.toLowerCase();
    const nameLower = item.name.toLowerCase();
    if (pkgLower.includes(q) || nameLower.includes(q)) return true;

    // Normalize common aliases: expand 'yt' to 'youtube'
    const qExpanded = q.replace(/\byt\b/g, 'youtube');
    if (pkgLower.includes(qExpanded) || nameLower.includes(qExpanded)) return true;

    // Match multi-word tokens
    const tokens = qExpanded.split(/\s+/).filter(Boolean);
    if (tokens.length > 1 && tokens.every(t => pkgLower.includes(t) || nameLower.includes(t))) return true;

    return false;
  });

  if (items.length === 0) {
    container.innerHTML = `<p style="padding: 16px; color: var(--text-muted); text-align: center;">No catalog apps match "${escapeHtml(query)}". You can use "Custom Package" above.</p>`;
    return;
  }

  // Sort: prefix matches first, or default apps prioritized on empty search
  if (q) {
    const qExpanded = q.replace(/\byt\b/g, 'youtube');
    items.sort((a, b) => {
      const aStarts = a.package.toLowerCase().startsWith(q) || a.name.toLowerCase().startsWith(q) || a.name.toLowerCase().startsWith(qExpanded);
      const bStarts = b.package.toLowerCase().startsWith(q) || b.name.toLowerCase().startsWith(q) || b.name.toLowerCase().startsWith(qExpanded);
      if (aStarts && !bStarts) return -1;
      if (!aStarts && bStarts) return 1;
      return a.name.localeCompare(b.name);
    });
  } else {
    items.sort((a, b) => {
      const aPrio = DEFAULT_PRIORITY_PKGS.indexOf(a.package);
      const bPrio = DEFAULT_PRIORITY_PKGS.indexOf(b.package);
      if (aPrio !== -1 && bPrio === -1) return -1;
      if (aPrio === -1 && bPrio !== -1) return 1;
      if (aPrio !== -1 && bPrio !== -1) return aPrio - bPrio;
      return a.name.localeCompare(b.name);
    });
  }

  const displayList = items.slice(0, 35);
  container.innerHTML = displayList.map(item => {
    const isSelected = (item.package === state.wizard.selectedPackage);
    const icon = item.icon_url ? `<img src="${item.icon_url}" style="width: 32px; height: 32px; border-radius: 6px; object-fit: cover;" onerror="this.src='https://github.com/ghost.png?size=32'">` : `<span class="material-symbols-rounded">android</span>`;

    return `
      <div class="catalog-item-card ${isSelected ? 'selected' : ''}" onclick="selectCatalogApp(this, '${item.package}', '${escapeHtml(item.name)}')" title="${escapeHtml(item.name)} (${item.package})">
        <div class="catalog-item-icon">
          ${icon}
        </div>
        <div class="catalog-item-info">
          <div class="catalog-item-name">${escapeHtml(item.name)}</div>
          <div class="catalog-item-pkg" title="${item.package}">${item.package}</div>
        </div>
        <span class="badge badge-info catalog-item-badge">${item.supported_repos?.length || 0} Repos</span>
      </div>
    `;
  }).join('');
}

function selectCatalogApp(cardEl, pkg, name) {
  if (typeof cardEl === 'string') {
    name = pkg;
    pkg = cardEl;
    cardEl = null;
  }
  state.wizard.selectedPackage = pkg;
  state.wizard.selectedAppName = name;

  document.querySelectorAll('.catalog-item-card').forEach(el => el.classList.remove('selected'));
  if (cardEl && cardEl.classList) {
    cardEl.classList.add('selected');
  } else if (window.event?.currentTarget) {
    window.event.currentTarget.classList.add('selected');
  }
}

async function loadSuggestedReposForPackage(pkg) {
  document.getElementById('wiz-selected-pkg-label').textContent = pkg;
  const list = document.getElementById('wiz-suggested-repos-list');
  list.innerHTML = `<p style="color: var(--text-dim);">Checking compatible repositories...</p>`;

  const catalog = await fetchMorpheCatalog();
  const info = catalog ? catalog[pkg] : null;
  state.wizard.suggestedRepos = (info?.repo_details && info.repo_details.length > 0)
    ? [...info.repo_details]
    : [];

  // Guarantee official Morphe repo is suggested for all Morphe default apps
  if (Object.keys(MORPHE_DEFAULT_APPS).includes(pkg)) {
    if (!state.wizard.suggestedRepos.some(r => r.repo_url === MORPHE_OFFICIAL_REPO_URL)) {
      state.wizard.suggestedRepos.unshift({
        repo_url: MORPHE_OFFICIAL_REPO_URL,
        name: 'Morphe',
        source: 'github',
        patch_count: MORPHE_DEFAULT_APPS[pkg]?.patch_count || 'Default',
      });
    }
  }

  const hasPreviousSelection = (state.wizard.lastLoadedPackage === pkg && state.wizard.repos.size > 0);
  const previouslySelected = hasPreviousSelection ? new Set(state.wizard.repos) : null;

  list.innerHTML = '';
  if (!hasPreviousSelection) {
    state.wizard.repos.clear();
  }
  state.wizard.lastLoadedPackage = pkg;

  const addedUrls = new Set();
  const suggestedInputs = [];
  const globalInputs = [];

  // Update master toolbar count badges
  const globalCountBadge = document.getElementById('wiz-global-count-badge');
  const globalEligible = state.globalRepos.filter(gUrl => !addedUrls.has(gUrl));
  if (globalCountBadge) globalCountBadge.textContent = globalEligible.length;

  const suggestedCountBadge = document.getElementById('wiz-suggested-count-badge');
  if (suggestedCountBadge) suggestedCountBadge.textContent = state.wizard.suggestedRepos.length;

  // 1. Recommended Repositories
  const suggestedSection = document.createElement('div');
  suggestedSection.style.marginBottom = '14px';

  const suggestedHeader = document.createElement('div');
  suggestedHeader.style.cssText = 'display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;';
  suggestedHeader.innerHTML = `
    <div style="font-size: 0.8125rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase;">Recommended Repositories (${state.wizard.suggestedRepos.length})</div>
  `;
  suggestedSection.appendChild(suggestedHeader);

  const suggestedWrap = document.createElement('div');
  suggestedWrap.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';

  if (state.wizard.suggestedRepos.length === 0) {
    suggestedWrap.innerHTML = `<p style="color: var(--text-muted); font-size: 0.8125rem;">No package-specific repos found in catalog. All global repositories are available below.</p>`;
  } else {
    state.wizard.suggestedRepos.forEach(repo => {
      addedUrls.add(repo.repo_url);
      const isChecked = hasPreviousSelection ? previouslySelected.has(repo.repo_url) : true;
      if (!hasPreviousSelection && isChecked) {
        state.wizard.repos.add(repo.repo_url);
      }
      const isOfficial = (repo.repo_url === MORPHE_OFFICIAL_REPO_URL);
      const badgeText = repo.patch_count ? `${repo.patch_count} patches` : (isOfficial ? 'Default' : 'Compatible');
      const el = createRepoCheckboxElement(repo.repo_url, cleanRepoSlug(repo.repo_url), badgeText, isChecked, () => {
        updateWizMasterCheckboxes();
      });
      suggestedInputs.push(el.querySelector('input'));
      suggestedWrap.appendChild(el);
    });
  }
  suggestedSection.appendChild(suggestedWrap);
  list.appendChild(suggestedSection);

  // 2. Other Available Repositories
  const globalSection = document.createElement('div');
  globalSection.style.marginBottom = '14px';

  // Recalculate globalEligible after adding suggested URLs
  const activeGlobalEligible = state.globalRepos.filter(gUrl => !addedUrls.has(gUrl));
  if (globalCountBadge) globalCountBadge.textContent = activeGlobalEligible.length;

  const globalHeader = document.createElement('div');
  globalHeader.style.cssText = 'display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;';
  globalHeader.innerHTML = `
    <div style="font-size: 0.8125rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase;">Other Available Repositories (${activeGlobalEligible.length})</div>
  `;
  globalSection.appendChild(globalHeader);

  const globalWrap = document.createElement('div');
  globalWrap.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';

  activeGlobalEligible.forEach(gUrl => {
    addedUrls.add(gUrl);
    const isChecked = hasPreviousSelection
      ? previouslySelected.has(gUrl)
      : (state.wizard.suggestedRepos.length === 0);
    if (!hasPreviousSelection && isChecked) state.wizard.repos.add(gUrl);
    const item = createRepoCheckboxElement(gUrl, cleanRepoSlug(gUrl), 'Repository', isChecked, () => {
      updateWizMasterCheckboxes();
    });
    globalInputs.push(item.querySelector('input'));
    globalWrap.appendChild(item);
  });
  globalSection.appendChild(globalWrap);
  list.appendChild(globalSection);

  function updateWizMasterCheckboxes() {
    const masterSuggested = document.getElementById('wiz-toggle-all-suggested');
    if (masterSuggested) {
      if (suggestedInputs.length === 0) {
        masterSuggested.checked = false;
        masterSuggested.disabled = true;
        masterSuggested.indeterminate = false;
      } else {
        masterSuggested.disabled = false;
        const checkedCount = suggestedInputs.filter(i => i.checked).length;
        masterSuggested.checked = checkedCount === suggestedInputs.length;
        masterSuggested.indeterminate = checkedCount > 0 && checkedCount < suggestedInputs.length;
      }
    }

    const masterGlobal = document.getElementById('wiz-toggle-all-global');
    if (masterGlobal) {
      if (globalInputs.length === 0) {
        masterGlobal.checked = false;
        masterGlobal.disabled = true;
        masterGlobal.indeterminate = false;
      } else {
        masterGlobal.disabled = false;
        const checkedCount = globalInputs.filter(i => i.checked).length;
        masterGlobal.checked = checkedCount === globalInputs.length;
        masterGlobal.indeterminate = checkedCount > 0 && checkedCount < globalInputs.length;
      }
    }
  }

  updateWizMasterCheckboxes();

  const masterSuggested = document.getElementById('wiz-toggle-all-suggested');
  if (masterSuggested) {
    masterSuggested.onchange = (e) => {
      const isChecked = e.target.checked;
      state.wizard.suggestedRepos.forEach(r => {
        if (isChecked) state.wizard.repos.add(r.repo_url);
        else state.wizard.repos.delete(r.repo_url);
      });
      suggestedInputs.forEach(i => { i.checked = isChecked; });
      updateWizMasterCheckboxes();
    };
  }

  const masterGlobal = document.getElementById('wiz-toggle-all-global');
  if (masterGlobal) {
    masterGlobal.onchange = (e) => {
      const isChecked = e.target.checked;
      activeGlobalEligible.forEach(gUrl => {
        if (isChecked) state.wizard.repos.add(gUrl);
        else state.wizard.repos.delete(gUrl);
      });
      globalInputs.forEach(i => { i.checked = isChecked; });
      updateWizMasterCheckboxes();
    };
  }
}

function addSuggestedRepoItem(repo, isChecked = true) {
  const list = document.getElementById('wiz-suggested-repos-list');
  const slug = cleanRepoSlug(repo.repo_url);
  const badgeText = repo.patch_count ? `${repo.patch_count} patches` : 'Compatible';

  const el = createRepoCheckboxElement(repo.repo_url, slug, badgeText, isChecked);
  list.appendChild(el);
  if (isChecked) state.wizard.repos.add(repo.repo_url);
}

function createRepoCheckboxElement(url, label, badgeText, isChecked = false, onToggleChange = null) {
  const div = document.createElement('label');
  div.style.cssText = 'display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--bg-surface-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); cursor: pointer; gap: 8px;';

  div.innerHTML = `
    <div style="display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1;">
      <input type="checkbox" value="${url}" ${isChecked ? 'checked' : ''} style="flex-shrink: 0;">
      <span class="repo-row-label" style="font-weight: 600; font-size: 0.875rem;">${escapeHtml(label)}</span>
      <a href="${escapeHtml(formatRepoUrl(url))}" target="_blank" rel="noopener noreferrer" class="repo-row-link" title="Open repository in new tab" onclick="event.stopPropagation()" style="flex-shrink: 0; display: inline-flex; align-items: center;">
        <span class="material-symbols-rounded" style="font-size: 14px;">open_in_new</span>
      </a>
    </div>
    <span class="badge badge-info" style="flex-shrink: 0;">${badgeText}</span>
  `;

  div.querySelector('input').addEventListener('change', (e) => {
    if (e.target.checked) {
      state.wizard.repos.add(url);
    } else {
      state.wizard.repos.delete(url);
    }
    if (typeof onToggleChange === 'function') onToggleChange();
  });

  return div;
}

function refreshWizardAliases() {
  const container = document.getElementById('wiz-aliases-list');
  if (!container) return;

  const rows = container.querySelectorAll('.alias-mapping-row');
  if (rows.length === 0) return;

  const preserved = [];
  rows.forEach(row => {
    const name = (row.querySelector('.alias-name')?.value || '').trim();
    const selectedPatches = [];
    row.querySelectorAll('.alias-repo-col').forEach(col => {
      const select = col.querySelector('.alias-repo-select');
      const custom = col.querySelector('.alias-repo-custom');
      let val = select ? select.value : '';
      if (val === '__custom__') val = (custom?.value || '').trim();
      if (val && !selectedPatches.includes(val)) selectedPatches.push(val);
    });
    if (name || selectedPatches.length > 0) {
      preserved.push({ name, aliases: selectedPatches });
    }
  });

  container.innerHTML = '';
  preserved.forEach(item => {
    renderAliasRow(true, item);
  });
}

function addAliasRow(aliasItem = null) {
  renderAliasRow(true, aliasItem);
}

function generateYamlPreview() {
  const pkg = state.wizard.selectedPackage;
  const repos = Array.from(state.wizard.repos);

  let yaml = `  - package_name: ${pkg}\n`;

  if (repos.length > 0) {
    yaml += `    repos:\n`;
    repos.forEach(r => {
      yaml += `      - ${r}\n`;
    });
  }

  // Aliases
  const validAliases = collectAliasData(true);
  if (validAliases.length > 0) {
    yaml += `    patch_aliases:\n`;
    validAliases.forEach(a => {
      yaml += `      - name: ${a.name}\n`;
      yaml += `        aliases:\n`;
      (a.aliases || []).forEach(target => {
        yaml += `          - ${target}\n`;
      });
    });
  }

  document.getElementById('wiz-yaml-preview').textContent = yaml;
}

async function fetchAppOptions(pkg) {
  try {
    const res = await fetch(`/api/options/${encodeURIComponent(pkg)}?_t=${Date.now()}`, { cache: 'no-store' });
    if (res.ok) {
      return await res.json();
    }
  } catch (err) {
    console.warn(`Could not load options for ${pkg}:`, err);
  }
  return { package: pkg, files: {} };
}

async function wizardSubmitApp() {
  const pkg = state.wizard.selectedPackage;
  const repos = Array.from(state.wizard.repos);
  const aliases = collectAliasData(true);

  const payload = {
    package: pkg,
    repos: repos,
    patch_aliases: aliases,
  };

  const btnSave = document.getElementById('btn-wiz-save');
  if (btnSave) {
    btnSave.disabled = true;
    btnSave.innerHTML = `<span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> <span>Saving...</span>`;
  }

  try {
    const res = await fetch('/api/config/app', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to save app');
    }

    // Save customized options files
    for (const [owner, repoData] of Object.entries(state.wizardPatches.repoPatches)) {
      if (!repoData.patches || repoData.patches.length === 0) continue;
      const patchesPayload = {};
      repoData.patches.forEach(p => {
        patchesPayload[p.name] = {
          enabled: p.enabled,
          ...(p.options ? { options: p.options } : {})
        };
      });

      await fetch(`/api/options/${encodeURIComponent(pkg)}/${encodeURIComponent(owner)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          meta: { package: pkg, repo: repoData.repoUrl },
          patches: patchesPayload
        })
      });
    }

    showToast(`Successfully added ${pkg} to config.yaml and configured patches!`, 'success');
    closeWizardModal();
    loadConfig();
    loadStatus();
  } catch (e) {
    showToast(`Error saving configuration: ${e.message}`, 'error');
  } finally {
    if (btnSave) {
      btnSave.disabled = false;
      btnSave.innerHTML = `<span class="material-symbols-rounded">save</span> <span>Save to config.yaml</span>`;
    }
  }
}

// ---------------------------------------------------------------------------
// Patch Options & Universal Definitions Schema
// ---------------------------------------------------------------------------
const PATCH_KNOWN_OPTIONS = {
  premiumVariant: {
    label: 'premiumVariant',
    description: 'Select subscription tier to unlock',
    type: 'select',
    choices: [
      { value: 'SUPER', label: 'SUPER (Super Duolingo)' },
      { value: 'MAX', label: 'MAX (Duolingo Max with AI)' }
    ]
  },
  installLocation: {
    label: 'installLocation',
    description: 'Target package installation location',
    type: 'select',
    choices: [
      { value: 'auto', label: 'auto (System Default)' },
      { value: 'internal', label: 'internal (Internal Storage)' },
      { value: 'preferExternal', label: 'preferExternal (SD / External)' }
    ]
  },
  timeFormat: {
    label: 'timeFormat',
    description: 'Display clock format',
    type: 'select',
    choices: [
      { value: '12', label: '12-hour clock (AM/PM)' },
      { value: '24', label: '24-hour military clock' }
    ]
  },
  widevineLevel: {
    label: 'widevineLevel',
    description: 'DRM security level spoofing',
    type: 'select',
    choices: [
      { value: 'L1', label: 'L1 (Hardware Enclave)' },
      { value: 'L2', label: 'L2' },
      { value: 'L3', label: 'L3 (Software Fallback)' }
    ]
  },
  securityLevel: {
    label: 'securityLevel',
    description: 'DRM security level',
    type: 'select',
    choices: [
      { value: 'L1', label: 'L1' },
      { value: 'L2', label: 'L2' },
      { value: 'L3', label: 'L3' }
    ]
  },
  hdcpLevel: {
    label: 'hdcpLevel',
    description: 'Content protection enforcement level',
    type: 'select',
    choices: [
      { value: 'HDCP_V1', label: 'HDCP 1.x' },
      { value: 'HDCP_V2', label: 'HDCP 2.0' },
      { value: 'HDCP_V2_2', label: 'HDCP 2.2' },
      { value: 'HDCP_NO_DIGITAL_OUTPUT', label: 'No Digital Output' }
    ]
  },
  runtimeOverlayButtonPosition: {
    label: 'runtimeOverlayButtonPosition',
    description: 'Screen position of floating button',
    type: 'select',
    choices: [
      { value: 'topRight', label: 'Top Right' },
      { value: 'topLeft', label: 'Top Left' },
      { value: 'bottomRight', label: 'Bottom Right' },
      { value: 'bottomLeft', label: 'Bottom Left' }
    ]
  },
  runtimeOverlayButtonShape: {
    label: 'runtimeOverlayButtonShape',
    description: 'Floating button appearance',
    type: 'select',
    choices: [
      { value: 'circle', label: 'Circle' },
      { value: 'rounded', label: 'Rounded' },
      { value: 'square', label: 'Square' }
    ]
  },
  runtimeOverlayTemperatureFormat: {
    label: 'runtimeOverlayTemperatureFormat',
    description: 'Overlay temperature unit',
    type: 'select',
    choices: [
      { value: 'celsius', label: 'Celsius (°C)' },
      { value: 'fahrenheit', label: 'Fahrenheit (°F)' }
    ]
  },
  statusBarIcons: {
    label: 'statusBarIcons',
    description: 'Status bar icon tinting',
    type: 'select',
    choices: [
      { value: 'system', label: 'System Default' },
      { value: 'white', label: 'White / Light' },
      { value: 'black', label: 'Black / Dark' }
    ]
  },
  darkThemeColor: {
    label: 'darkThemeColor',
    description: 'Dark mode tinting palette',
    type: 'select',
    choices: [
      { value: 'in-app', label: 'Default App Dark' },
      { value: 'black', label: 'Black' },
      { value: 'amoled', label: 'AMOLED Pure Black (#000000)' },
      { value: 'pitch_black', label: 'Pitch Black' }
    ]
  },
  startupToastDuration: {
    label: 'startupToastDuration',
    description: 'Toast display duration',
    type: 'select',
    choices: [
      { value: 'short', label: 'Short' },
      { value: 'long', label: 'Long' }
    ]
  }
};

const KNOWN_UNIVERSAL_PATCH_NAMES = new Set([
  'clone app',
  'clone',
  'change package name',
  'hide app icon',
  'remove app icon',
  'change app icon',
  'change app name',
  'rename app',
  'enable debug',
  'enable debug mode',
  'enable android debugging',
  'enable webview debugging',
  'make app debuggable',
  'spoof signature',
  'spoof app signature',
  'spoof signature verification',
  'spoof signature match',
  'spoof install source',
  'spoof play store install source',
  'spoof device model',
  'spoof build fingerprint',
  'spoof wi-fi connection',
  'spoof sim provider',
  'spoof telephony ids',
  'spoof advertising id',
  'spoof play integrity',
  'provide original app certificate',
  'fix firebase after re-signing',
  'microg integration',
  'gmscore support',
  'gmscore support (microg)',
  'disable pairip license check',
  'disable pairip protection',
  'bypass pairip license check',
  'custom branding',
  'predictive back gesture',
  'hex',
  'remove screenshot restriction',
  'remove screenshot detection',
  'allow screenshots',
  'disable secure flag',
  'bypass root check',
  'disable root checks',
  'bypass adb/developer options detection',
  'bypass emulator detection',
  'bypass forced updates',
  'bypass vpn detection',
  'bypass license verification',
  'bypass okhttp pinning',
  'trust user certificates',
  'override certificate pinning',
  'disable sentry telemetry',
  'disable firebase(google) analytics',
  'deactivate firebase analytics',
  'deactivate firebase performance monitoring',
  'disable analytics',
  'disable analytics & ad tracking',
  'disable crash reporters',
  'disable telemetry',
  'block ads, trackers, and analytics',
  'always enable copy',
  'spoof client',
  'disable dynamic app icon',
  'widevine l3 fallback',
  'spoof wifi connection',
  'disable splash screen',
  'skip splash screen',
  'apk junk cleanup',
  'png optimizer',
  'remove debug info',
  'remove duplicate graphics',
  'remove languages',
  'strip translations',
  'export all activities',
  'allow background activity',
  'keep screen on',
  'force resizable activity',
  'immersive fullscreen',
  'transparent system bars',
  'unlock rotation',
  'allow cleartext traffic',
  'allow mixed content',
  'ensure internet permission',
  'ensure notification permission',
  'ensure storage permissions',
  'unlock revenuecat',
  'revenuecat',
]);

function determinePatchScope(patchName, patchObj) {
  if (patchObj?.isUniversal === true || patchObj?.scope === 'universal') return 'universal';
  if (patchObj?.isUniversal === false && patchObj?.scope === 'app') return 'app';
  if (Array.isArray(patchObj?.targets) && patchObj.targets.length === 0) return 'universal';
  if (Array.isArray(patchObj?.compatiblePackages) && patchObj.compatiblePackages.length === 0) return 'universal';

  const norm = (patchName || '').toLowerCase().trim();
  if (!norm) return 'app';
  if (KNOWN_UNIVERSAL_PATCH_NAMES.has(norm)) return 'universal';

  // Pattern matching heuristics for universal patches
  if (
    norm.startsWith('clone') ||
    norm.startsWith('change package name') ||
    norm.startsWith('spoof ') ||
    norm.includes('pairip') ||
    norm.includes('microg') ||
    norm.includes('gmscore') ||
    norm.includes('screenshot') ||
    norm.includes('license verification') ||
    norm.includes('root check') ||
    norm.includes('debuggable') ||
    norm.includes('user certificate') ||
    norm.includes('certificate pinning') ||
    norm.includes('revenuecat')
  ) {
    return 'universal';
  }
  return 'app';
}

function getPatchOptionDefinitions(patch) {
  const result = [];
  const existingValues = (patch.options && typeof patch.options === 'object' && !Array.isArray(patch.options))
    ? patch.options
    : {};

  if (Array.isArray(patch.schemaOptions)) {
    patch.schemaOptions.forEach(opt => {
      const key = opt.key || opt.name;
      if (!key) return;
      const val = (existingValues[key] !== undefined) ? existingValues[key] : (opt.default ?? opt.value ?? '');
      const known = PATCH_KNOWN_OPTIONS[key];
      let choices = opt.possibleValues || opt.values || known?.choices || null;
      let type = known?.type || (choices ? 'select' : (typeof val === 'boolean' || opt.type === 'Boolean' ? 'boolean' : (typeof val === 'number' || opt.type === 'Int' ? 'number' : 'string')));
      result.push({
        key,
        label: opt.title || known?.label || key,
        description: opt.description || known?.description || '',
        type,
        value: val,
        choices
      });
    });
  }

  for (const [key, val] of Object.entries(existingValues)) {
    if (result.some(r => r.key === key)) continue;
    const known = PATCH_KNOWN_OPTIONS[key];
    let type = 'string';
    let choices = known?.choices || null;
    if (typeof val === 'boolean') {
      type = 'boolean';
    } else if (typeof val === 'number') {
      type = 'number';
    } else if (known?.type === 'select' || choices) {
      type = 'select';
    }
    result.push({
      key,
      label: known?.label || key,
      description: known?.description || '',
      type,
      value: val,
      choices
    });
  }

  return result;
}

function renderPatchSubOptions(patch, owner, isWizard) {
  const optDefs = getPatchOptionDefinitions(patch);
  if (optDefs.length === 0) return '';

  const rows = optDefs.map(def => {
    let controlHtml = '';
    const updateFn = isWizard ? 'updateWizardPatchOption' : 'updateEditPatchOption';
    const escapedKey = escapeHtml(def.key);
    const escapedOwner = escapeHtml(owner);
    const escapedPatch = escapeHtml(patch.name);

    if (def.type === 'select' && Array.isArray(def.choices)) {
      const optsHtml = def.choices.map(c => {
        const val = typeof c === 'string' ? c : c.value;
        const lbl = typeof c === 'string' ? c : (c.label || c.value);
        const isSel = String(def.value) === String(val);
        return `<option value="${escapeHtml(val)}" ${isSel ? 'selected' : ''}>${escapeHtml(lbl)}</option>`;
      }).join('');
      controlHtml = `
        <select class="form-control form-control-sm patch-option-input" style="width: auto; min-width: 140px; font-size: 0.8125rem;"
          onchange="${updateFn}('${escapedOwner}', '${escapedPatch}', '${escapedKey}', this.value)">
          ${optsHtml}
        </select>
      `;
    } else if (def.type === 'boolean') {
      const isChecked = def.value === true;
      controlHtml = `
        <label class="switch switch-sm" title="${escapedKey}">
          <input type="checkbox" ${isChecked ? 'checked' : ''}
            onchange="${updateFn}('${escapedOwner}', '${escapedPatch}', '${escapedKey}', this.checked)">
          <span class="switch-slider"></span>
        </label>
      `;
    } else if (def.type === 'number') {
      controlHtml = `
        <input type="number" class="form-control form-control-sm patch-option-input" style="width: 100px; font-size: 0.8125rem;"
          value="${escapeHtml(String(def.value ?? ''))}"
          onchange="${updateFn}('${escapedOwner}', '${escapedPatch}', '${escapedKey}', Number(this.value))">
      `;
    } else {
      controlHtml = `
        <input type="text" class="form-control form-control-sm patch-option-input" style="width: 160px; font-size: 0.8125rem;"
          value="${escapeHtml(String(def.value ?? ''))}" placeholder="(Default)"
          onchange="${updateFn}('${escapedOwner}', '${escapedPatch}', '${escapedKey}', this.value)">
      `;
    }

    return `
      <div class="patch-option-row">
        <div class="patch-option-label-wrap">
          <span class="patch-option-key">${escapedKey}</span>
          ${def.description ? `<span class="patch-option-desc">${escapeHtml(def.description)}</span>` : ''}
        </div>
        <div class="patch-option-control">
          ${controlHtml}
        </div>
      </div>
    `;
  }).join('');

  return `<div class="patch-card-options">${rows}</div>`;
}

function updateEditPatchOption(owner, patchName, key, value) {
  const repoData = state.editApp.repoPatches[owner];
  if (!repoData) return;
  const p = repoData.patches.find(x => x.name === patchName);
  if (p) {
    if (!p.options || typeof p.options !== 'object' || Array.isArray(p.options)) {
      p.options = {};
    }
    p.options[key] = value;
  }
}

function updateWizardPatchOption(owner, patchName, key, value) {
  const repoData = state.wizardPatches.repoPatches[owner];
  if (!repoData) return;
  const p = repoData.patches.find(x => x.name === patchName);
  if (p) {
    if (!p.options || typeof p.options !== 'object' || Array.isArray(p.options)) {
      p.options = {};
    }
    p.options[key] = value;
  }
}

function renderPatchCardHtml(patch, isWizard) {
  const toggleFn = isWizard ? 'toggleWizardPatch' : 'toggleEditPatch';
  const owner = patch.repoOwner || '';
  const escapedOwner = escapeHtml(owner);
  const escapedName = escapeHtml(patch.name);
  const scope = patch.scope || 'app';
  const isAppScope = scope === 'app';

  return `
    <div class="patch-card ${patch.enabled ? 'active' : ''}">
      <div class="patch-header-row">
        <div class="patch-title">
          <span class="material-symbols-rounded" style="font-size: 18px; color: ${patch.enabled ? 'var(--primary-cyan)' : 'var(--text-dim)'}; flex-shrink: 0;">
            ${patch.enabled ? 'check_circle' : 'radio_button_unchecked'}
          </span>
          <span class="patch-title-text" style="font-weight: 600;">${escapedName}</span>
          ${patch.defaultEnabled ? '<span class="badge badge-info" style="font-size: 0.6875rem;">Default</span>' : ''}
          <span class="patch-scope-chip ${isAppScope ? 'app-specific' : 'universal'}">
            <span class="material-symbols-rounded" style="font-size: 12px;">${isAppScope ? 'category' : 'public'}</span>
            <span>${isAppScope ? 'App-Specific' : 'Universal'}</span>
          </span>
          <span class="patch-repo-badge">
            <span class="material-symbols-rounded" style="font-size: 12px;">source</span>
            <span>${escapedOwner}</span>
          </span>
        </div>
        <label class="switch" title="${patch.enabled ? 'Enabled' : 'Disabled'}" style="flex-shrink: 0;">
          <input type="checkbox" ${patch.enabled ? 'checked' : ''} onchange="${toggleFn}('${escapedOwner}', '${escapedName}', this.checked)">
          <span class="switch-slider"></span>
        </label>
      </div>
      ${patch.description ? `<div class="patch-description">${escapeHtml(patch.description)}</div>` : ''}
      ${renderPatchSubOptions(patch, owner, isWizard)}
    </div>
  `;
}

function getFilteredPatches(isWizard) {
  const appState = isWizard ? state.wizardPatches : state.editApp;
  const owner = appState.selectedRepoOwner || 'all';
  let patches = [];

  if (owner === 'all') {
    Object.values(appState.repoPatches).forEach(repoData => {
      if (Array.isArray(repoData.patches)) {
        patches.push(...repoData.patches);
      }
    });
  } else if (appState.repoPatches[owner]) {
    patches = [...(appState.repoPatches[owner].patches || [])];
  }

  const totalBeforeFilters = patches.length;
  const totalEnabledBeforeFilters = patches.filter(p => p.enabled).length;

  // Search filter
  if (appState.patchSearch) {
    const q = appState.patchSearch.toLowerCase();
    patches = patches.filter(p =>
      p.name.toLowerCase().includes(q) ||
      (p.description && p.description.toLowerCase().includes(q)) ||
      (p.repoOwner && p.repoOwner.toLowerCase().includes(q)) ||
      (p.options && typeof p.options === 'object' && Object.keys(p.options).some(k => k.toLowerCase().includes(q)))
    );
  }

  // Status filter
  if (appState.patchFilter === 'enabled') {
    patches = patches.filter(p => p.enabled);
  } else if (appState.patchFilter === 'disabled') {
    patches = patches.filter(p => !p.enabled);
  }

  // Scope filter
  if (appState.scopeFilter === 'app') {
    patches = patches.filter(p => (p.scope || 'app') === 'app');
  } else if (appState.scopeFilter === 'universal') {
    patches = patches.filter(p => p.scope === 'universal');
  }

  return {
    patches,
    totalCount: totalBeforeFilters,
    enabledCount: totalEnabledBeforeFilters,
  };
}

function collectAliasData(isWizard) {
  const container = document.getElementById(isWizard ? 'wiz-aliases-list' : 'edit-aliases-list');
  if (!container) return [];
  const aliases = [];
  container.querySelectorAll('.alias-mapping-row').forEach(row => {
    const name = (row.querySelector('.alias-name')?.value || '').trim();
    if (!name) return;
    const selectedPatches = [];
    row.querySelectorAll('.alias-repo-col').forEach(col => {
      const select = col.querySelector('.alias-repo-select');
      const custom = col.querySelector('.alias-repo-custom');
      let val = select ? select.value : '';
      if (val === '__custom__') {
        val = (custom?.value || '').trim();
      }
      if (val && !selectedPatches.includes(val)) {
        selectedPatches.push(val);
      }
    });
    if (selectedPatches.length > 0) {
      aliases.push({
        name: name,
        aliases: selectedPatches
      });
    }
  });
  return aliases;
}

function renderAliasRow(isWizard, aliasItem = null) {
  const container = document.getElementById(isWizard ? 'wiz-aliases-list' : 'edit-aliases-list');
  if (!container) return;

  const repoPatches = isWizard ? state.wizardPatches.repoPatches : state.editApp.repoPatches;
  let activeRepos = [];
  if (isWizard) {
    const selected = Array.from(state.wizard.repos);
    activeRepos = selected.length > 0 ? selected : state.globalRepos;
  } else {
    activeRepos = state.editApp.repos.size > 0 ? Array.from(state.editApp.repos) : state.globalRepos;
  }

  let owners = activeRepos.map(extractRepoOwner).filter(Boolean);
  owners = [...new Set(owners)];
  if (owners.length === 0) {
    owners = Object.keys(repoPatches);
  }
  if (owners.length === 0) {
    owners = ['Repo 1', 'Repo 2'];
  }

  const row = document.createElement('div');
  row.className = 'alias-mapping-row';
  row.style.cssText = 'display: flex; gap: 10px; align-items: flex-start; margin-bottom: 10px; flex-wrap: wrap; background: rgba(0,0,0,0.22); padding: 12px 14px; border-radius: var(--radius-md); border: 1px solid var(--border-subtle);';

  const nameVal = aliasItem?.name || '';
  let repoColsHtml = '';
  const assignedAliases = new Set();

  owners.forEach(owner => {
    const rData = repoPatches[owner];
    const patches = rData?.patches || [];

    let optionsHtml = '<option value="">-- None / Skip --</option>';
    let matchedPatch = '';

    if (aliasItem && Array.isArray(aliasItem.aliases)) {
      for (const al of aliasItem.aliases) {
        if (!assignedAliases.has(al)) {
          const found = patches.find(p => p.name.toLowerCase() === al.toLowerCase());
          if (found) {
            matchedPatch = found.name;
            assignedAliases.add(al);
            break;
          }
        }
      }
    }

    // Only show patches that are enabled (or explicitly matched for an existing alias configuration)
    const enabledPatches = patches.filter(p => p.enabled || (matchedPatch && p.name === matchedPatch));

    if (enabledPatches.length === 0) {
      optionsHtml += '<option value="" disabled>(No enabled patches)</option>';
    } else {
      enabledPatches.forEach(p => {
        const isSel = (p.name === matchedPatch);
        optionsHtml += `<option value="${escapeHtml(p.name)}" ${isSel ? 'selected' : ''}>${escapeHtml(p.name)}</option>`;
      });
    }

    optionsHtml += `<option value="__custom__">-- Custom / Unlisted --</option>`;

    repoColsHtml += `
      <div style="flex: 1; min-width: 170px; display: flex; flex-direction: column; gap: 4px;" class="alias-repo-col" data-owner="${escapeHtml(owner)}">
        <label style="font-size: 0.75rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase;">[${escapeHtml(owner)}] PATCH</label>
        <select class="form-control alias-repo-select" style="font-size: 0.8125rem;">
          ${optionsHtml}
        </select>
        <input type="text" class="form-control alias-repo-custom hidden" style="font-size: 0.8125rem; margin-top: 4px;" placeholder="Type custom patch name...">
      </div>
    `;
  });

  row.innerHTML = `
    <div class="alias-name-col" style="flex: 1.2; min-width: 180px; display: flex; flex-direction: column; gap: 4px;">
      <label style="font-size: 0.75rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase;">ALIAS NAME</label>
      <input type="text" class="form-control alias-name" placeholder="e.g. Unlock Premium" value="${escapeHtml(nameVal)}" style="font-size: 0.8125rem;">
    </div>
    ${repoColsHtml}
    <div style="padding-top: 22px;">
      <button class="btn btn-danger btn-sm btn-icon-only" type="button" title="Remove alias"><span class="material-symbols-rounded">close</span></button>
    </div>
  `;

  // Attach event handlers
  row.querySelector('.alias-name')?.addEventListener('input', () => {
    if (isWizard) generateYamlPreview();
  });

  row.querySelectorAll('.alias-repo-col').forEach(col => {
    const sel = col.querySelector('.alias-repo-select');
    const custom = col.querySelector('.alias-repo-custom');
    if (!sel || !custom) return;

    sel.addEventListener('change', (e) => {
      if (e.target.value === '__custom__') {
        custom.classList.remove('hidden');
        custom.focus();
      } else {
        custom.classList.add('hidden');
        custom.value = '';
      }
      if (isWizard) generateYamlPreview();
    });

    custom.addEventListener('input', () => {
      if (isWizard) generateYamlPreview();
    });
  });

  // Assign leftover aliases in aliasItem.aliases that did not match patch names by exact string
  if (aliasItem && Array.isArray(aliasItem.aliases)) {
    const unassigned = aliasItem.aliases.filter(al => !assignedAliases.has(al));
    if (unassigned.length > 0) {
      const cols = row.querySelectorAll('.alias-repo-col');
      cols.forEach(col => {
        const sel = col.querySelector('.alias-repo-select');
        const custom = col.querySelector('.alias-repo-custom');
        if (sel && sel.value === '' && unassigned.length > 0) {
          const nextVal = unassigned.shift();
          sel.value = '__custom__';
          custom.classList.remove('hidden');
          custom.value = nextVal;
        }
      });
    }
  }

  row.querySelector('.btn-danger')?.addEventListener('click', () => {
    row.remove();
    if (isWizard) generateYamlPreview();
  });

  container.appendChild(row);
}

function formatErrorMessage(errData, fallbackStatus = 500) {
  if (!errData) return `Server returned status ${fallbackStatus}`;
  if (typeof errData === 'string') return errData;
  if (typeof errData.detail === 'string') return errData.detail;
  if (Array.isArray(errData.detail)) {
    return errData.detail.map(d => (d.msg ? `${d.loc ? d.loc.join('.') + ': ' : ''}${d.msg}` : JSON.stringify(d))).join('; ');
  }
  if (errData.detail && typeof errData.detail === 'object') {
    return errData.detail.message || JSON.stringify(errData.detail);
  }
  if (errData.error) return typeof errData.error === 'string' ? errData.error : JSON.stringify(errData.error);
  if (errData.message) return errData.message;
  return JSON.stringify(errData);
}

function openPatchFetchModal(pkg) {
  const modal = document.getElementById('modal-patch-fetch');
  if (!modal) return;
  const pkgEl = document.getElementById('patch-fetch-modal-pkg');
  if (pkgEl) pkgEl.textContent = pkg;

  // Reset steps
  updatePatchFetchStep('cache', 'Checking...', 'badge-info');
  updatePatchFetchStep('bundle', 'Waiting', 'badge-neutral');
  updatePatchFetchStep('network', 'Waiting', 'badge-neutral');

  // Reset status card
  const spinner = document.getElementById('patch-fetch-spinner');
  if (spinner) spinner.innerHTML = `<span class="material-symbols-rounded" style="font-size: 28px; color: var(--primary-cyan); animation: spin 1s infinite linear;">sync</span>`;

  const titleEl = document.getElementById('patch-fetch-status-title');
  if (titleEl) titleEl.textContent = 'Discovering Patch Options...';

  const subEl = document.getElementById('patch-fetch-status-subtitle');
  if (subEl) subEl.textContent = `Connecting to server and checking repositories for ${pkg}...`;

  const retryBtn = document.getElementById('btn-patch-fetch-retry');
  if (retryBtn) retryBtn.style.display = 'none';

  const footerSummary = document.getElementById('patch-fetch-footer-summary');
  if (footerSummary) footerSummary.textContent = 'Working...';

  clearPatchFetchLogs();
  modal.classList.add('open');
}

function closePatchFetchModal() {
  document.getElementById('modal-patch-fetch')?.classList.remove('open');
}

function updatePatchFetchStep(stepName, text, badgeClass) {
  const badge = document.getElementById(`step-tier-${stepName}-badge`);
  if (!badge) return;
  badge.className = `badge ${badgeClass}`;
  badge.textContent = text;
}

function appendPatchFetchLog(message, type = 'info') {
  const consoleEl = document.getElementById('patch-fetch-log-console');
  if (!consoleEl) return;
  const line = document.createElement('div');
  line.className = 'terminal-line';
  if (type === 'error') line.style.color = '#f87171';
  else if (type === 'success') line.style.color = '#34d399';
  else if (type === 'warning') line.style.color = '#fbbf24';
  else line.style.color = '#e2e8f0';

  line.textContent = message;
  consoleEl.appendChild(line);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function clearPatchFetchLogs() {
  const consoleEl = document.getElementById('patch-fetch-log-console');
  if (consoleEl) consoleEl.innerHTML = '';
}

function copyPatchFetchLogs() {
  const consoleEl = document.getElementById('patch-fetch-log-console');
  if (!consoleEl) return;
  navigator.clipboard.writeText(consoleEl.textContent || '')
    .then(() => showToast('Execution log copied to clipboard', 'info'))
    .catch(() => showToast('Failed to copy log', 'warning'));
}

async function handleSeeAvailablePatches(isWizard, force = false) {
  const pkg = isWizard ? state.wizard.selectedPackage : state.editApp.package;
  if (!pkg) {
    showToast('Please select a package first.', 'warning');
    return;
  }

  state.lastPatchFetchContext = { isWizard, pkg };

  const selectedRepos = isWizard
    ? Array.from(state.wizard.repos)
    : (state.editApp.repos.size > 0 ? Array.from(state.editApp.repos) : state.globalRepos);
  const activeRepos = selectedRepos.length > 0 ? selectedRepos : state.globalRepos;

  // Open the centered verbose progress & log modal
  openPatchFetchModal(pkg);
  appendPatchFetchLog(`[Init] Target Package: ${pkg}`);
  appendPatchFetchLog(`[Init] Checking ${activeRepos.length} repository source(s): ${activeRepos.map(cleanRepoSlug).join(', ')}`);
  if (force) appendPatchFetchLog(`[Init] Force mode enabled: bypassing cached options files`);

  const btn = document.getElementById(isWizard ? 'btn-wiz-download-options' : 'btn-edit-download-options');
  const originalHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> <span>Checking options...</span>`;
  }

  try {
    const res = await fetch(`/api/options-fetch/${encodeURIComponent(pkg)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repos: activeRepos, force: force })
    });

    let data;
    try {
      data = await res.json();
    } catch (parseErr) {
      throw new Error(`Invalid response from server (HTTP ${res.status})`);
    }

    if (!res.ok) {
      const errMsg = formatErrorMessage(data, res.status);
      throw new Error(errMsg);
    }

    // Append server logs to the log console
    if (Array.isArray(data.logs)) {
      data.logs.forEach(line => {
        const lineType = line.includes('Error') ? 'error' : (line.includes('Success') || line.includes('Created') ? 'success' : (line.includes('Warning') ? 'warning' : 'info'));
        appendPatchFetchLog(line, lineType);
      });
    }

    // Update status badges based on repo_statuses
    const statuses = data.repo_statuses || {};
    let hasExisting = false;
    let hasGenerated = false;
    let hasNotFound = false;

    Object.values(statuses).forEach(s => {
      if (s.source === 'options_file') hasExisting = true;
      if (s.source === 'cached_mpp' || s.source === 'downloaded_mpp') hasGenerated = true;
      if (s.source === 'catalog_fallback') hasGenerated = true;
      if (s.status === 'not_found') hasNotFound = true;
    });

    updatePatchFetchStep('cache', hasExisting ? 'Found' : 'Checked', hasExisting ? 'badge-success' : 'badge-neutral');
    updatePatchFetchStep('bundle', hasGenerated ? 'Success' : 'Checked', hasGenerated ? 'badge-success' : 'badge-neutral');
    updatePatchFetchStep('network', hasNotFound ? 'Partial' : (hasGenerated ? 'Loaded' : 'Checked'), hasNotFound ? 'badge-warning' : 'badge-success');

    // Reload patches views with the newly saved or existing server options
    if (isWizard) {
      await loadWizardPatches();
    } else {
      await loadEditAppPatches();
      renderEditRulesTab(); // refresh alias dropdowns with fresh patches
    }

    const totalPatches = data.total_patches ?? 0;
    const isSuccess = data.success !== false && (totalPatches > 0 || hasExisting || hasGenerated);

    const spinner = document.getElementById('patch-fetch-spinner');
    const titleEl = document.getElementById('patch-fetch-status-title');
    const subEl = document.getElementById('patch-fetch-status-subtitle');
    const retryBtn = document.getElementById('btn-patch-fetch-retry');
    const footerSummary = document.getElementById('patch-fetch-footer-summary');

    if (isSuccess) {
      if (spinner) spinner.innerHTML = `<span class="material-symbols-rounded" style="font-size: 28px; color: var(--color-success);">check_circle</span>`;
      if (titleEl) titleEl.textContent = 'Patches Loaded Successfully!';
      if (subEl) subEl.textContent = `Discovered ${totalPatches} available patches across repositories for ${pkg}.`;
      if (footerSummary) footerSummary.textContent = `${totalPatches} patches ready to configure`;
      if (retryBtn) retryBtn.style.display = 'inline-flex';
      showToast(`Loaded ${totalPatches} available patches for ${pkg}!`, 'success');
    } else {
      if (spinner) spinner.innerHTML = `<span class="material-symbols-rounded" style="font-size: 28px; color: var(--color-warning);">warning</span>`;
      if (titleEl) titleEl.textContent = 'No Patches Generated';
      const errNote = data.error || 'No matching patch options could be found in the configured repositories.';
      if (subEl) subEl.textContent = errNote;
      if (footerSummary) footerSummary.textContent = '0 patches found';
      if (retryBtn) retryBtn.style.display = 'inline-flex';
      appendPatchFetchLog(`[Result] Warning: ${errNote}`, 'warning');
      showToast(`No patches found for ${pkg}`, 'warning');
    }

  } catch (err) {
    const errorMsg = formatErrorMessage(err);
    console.error('Error fetching/downloading options to server:', err);
    appendPatchFetchLog(`[Fatal Error] ${errorMsg}`, 'error');

    const spinner = document.getElementById('patch-fetch-spinner');
    if (spinner) spinner.innerHTML = `<span class="material-symbols-rounded" style="font-size: 28px; color: var(--color-danger);">error</span>`;

    const titleEl = document.getElementById('patch-fetch-status-title');
    if (titleEl) titleEl.textContent = 'Failed to Fetch Patches';

    const subEl = document.getElementById('patch-fetch-status-subtitle');
    if (subEl) subEl.textContent = errorMsg;

    const retryBtn = document.getElementById('btn-patch-fetch-retry');
    if (retryBtn) retryBtn.style.display = 'inline-flex';

    const footerSummary = document.getElementById('patch-fetch-footer-summary');
    if (footerSummary) footerSummary.textContent = 'Error during discovery';

    showToast(`Error loading patches: ${errorMsg}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }
}


// ---------------------------------------------------------------------------
// Wizard Step 3: Configure Patches
// ---------------------------------------------------------------------------
async function loadWizardPatches() {
  const pkg = state.wizard.selectedPackage;
  const pkgLabel = document.getElementById('wiz-patches-pkg-label');
  if (pkgLabel) pkgLabel.textContent = pkg;

  const listEl = document.getElementById('wiz-patches-list');
  if (listEl) {
    listEl.innerHTML = `<div style="text-align: center; padding: 28px; color: var(--text-dim);"><span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> Loading patch catalog...</div>`;
  }

  const selectedRepos = Array.from(state.wizard.repos);
  const activeRepos = selectedRepos.length > 0 ? selectedRepos : state.globalRepos;

  let [existingOptions, catalog] = await Promise.all([
    fetchAppOptions(pkg),
    fetchMorpheCatalog()
  ]);

  // Ensure options files are generated via Morphe options-create workflow for any missing repos
  const missingRepos = activeRepos.filter(repoUrl => {
    const owner = extractRepoOwner(repoUrl);
    return !existingOptions.files || !existingOptions.files[owner];
  });

  if (missingRepos.length > 0) {
    const missingNames = missingRepos.map(cleanRepoSlug).join(', ');
    updateWizardLoadingModal(
      'Downloading & Populating Patches...',
      `Downloading release bundle & populating patches for ${missingNames} via Morphe CLI...`
    );
    try {
      const fetchRes = await fetch(`/api/options-fetch/${encodeURIComponent(pkg)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repos: missingRepos, force: false })
      });
      if (fetchRes.ok) {
        existingOptions = await fetchAppOptions(pkg);
      }
    } catch (e) {
      console.warn('Auto options-fetch failed, proceeding with catalog fallback:', e);
    }
  }

  state.wizardPatches.repoPatches = {};

  activeRepos.forEach(repoUrl => {
    const owner = extractRepoOwner(repoUrl);
    const savedPatches = existingOptions.files?.[owner]?.patches || {};
    const catPatches = catalog?.[pkg]?.patches_by_repo?.[repoUrl] || [];

    const patchList = [];
    const seenNames = new Set();

    catPatches.forEach(cp => {
      seenNames.add(cp.name);
      const scope = determinePatchScope(cp.name, cp);
      const isUniv = scope === 'universal' || cp.isUniversal === true;
      const isDefault = isUniv ? false : Boolean(cp.default === true || cp.use === true);
      const saved = savedPatches[cp.name];
      const isEnabled = (saved !== undefined && saved.enabled !== undefined) ? (saved.enabled === true) : isDefault;

      let initialOptions = {};
      if (Array.isArray(cp.options)) {
        cp.options.forEach(opt => {
          if (opt.key) initialOptions[opt.key] = opt.default ?? opt.value ?? '';
        });
      } else if (cp.options && typeof cp.options === 'object' && !Array.isArray(cp.options)) {
        initialOptions = JSON.parse(JSON.stringify(cp.options));
      }
      if (saved?.options && typeof saved.options === 'object' && !Array.isArray(saved.options)) {
        Object.assign(initialOptions, JSON.parse(JSON.stringify(saved.options)));
      }
      if (Object.keys(initialOptions).length === 0) {
        initialOptions = null;
      }

      patchList.push({
        name: cp.name,
        description: cp.description,
        enabled: isEnabled,
        defaultEnabled: isDefault,
        options: initialOptions,
        schemaOptions: Array.isArray(cp.options) ? cp.options : null,
        repoOwner: owner,
        repoUrl: repoUrl,
        scope: determinePatchScope(cp.name, cp),
      });
    });

    for (const [sName, sData] of Object.entries(savedPatches)) {
      if (!seenNames.has(sName)) {
        seenNames.add(sName);
        const isEnabled = (sData.enabled === true);
        patchList.push({
          name: sName,
          description: '(Saved patch)',
          enabled: isEnabled,
          defaultEnabled: false,
          options: (sData.options && typeof sData.options === 'object') ? JSON.parse(JSON.stringify(sData.options)) : null,
          schemaOptions: null,
          repoOwner: owner,
          repoUrl: repoUrl,
          scope: determinePatchScope(sName, sData),
        });
      }
    }

    state.wizardPatches.repoPatches[owner] = {
      repoUrl: repoUrl,
      owner: owner,
      patches: patchList
    };
  });

  const owners = Object.keys(state.wizardPatches.repoPatches);
  const repoSelect = document.getElementById('wiz-patch-repo-filter');
  if (repoSelect) {
    repoSelect.innerHTML = `<option value="all">All Repositories (${owners.length})</option>` +
      owners.map(o => {
        const r = state.wizardPatches.repoPatches[o];
        return `<option value="${o}">${o} (${cleanRepoSlug(r.repoUrl)})</option>`;
      }).join('');
    if (!state.wizardPatches.selectedRepoOwner || (state.wizardPatches.selectedRepoOwner !== 'all' && !owners.includes(state.wizardPatches.selectedRepoOwner))) {
      state.wizardPatches.selectedRepoOwner = 'all';
    }
    repoSelect.value = state.wizardPatches.selectedRepoOwner;
  }

  renderWizardPatchesList();
}

function renderWizardPatchesList() {
  const container = document.getElementById('wiz-patches-list');
  if (!container) return;

  const { patches, totalCount, enabledCount } = getFilteredPatches(true);

  if (totalCount === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 28px; background: var(--bg-surface-elevated); border-radius: var(--radius-md); border: 1px dashed var(--border-subtle);">
        <span class="material-symbols-rounded" style="font-size: 36px; color: var(--text-dim);">tune</span>
        <div style="font-weight: 600; margin-top: 8px;">No Patches Loaded Yet</div>
        <p style="color: var(--text-muted); font-size: 0.8125rem; margin-top: 4px;">
          Check server options or fetch them directly from the repository.
        </p>
        <button type="button" class="btn btn-primary btn-sm" style="margin-top: 12px;" onclick="handleSeeAvailablePatches(true)">
          <span class="material-symbols-rounded">visibility</span>
          <span>See Available Patches</span>
        </button>
      </div>
    `;
    updateWizardPatchCounts(0, 0);
    return;
  }

  if (patches.length === 0) {
    container.innerHTML = `<p style="padding: 24px; text-align: center; color: var(--text-muted);">No patches match your filter.</p>`;
    updateWizardPatchCounts(enabledCount, totalCount);
    return;
  }

  container.innerHTML = patches.map(patch => renderPatchCardHtml(patch, true)).join('');
  updateWizardPatchCounts(enabledCount, totalCount);
}

function updateWizardPatchCounts(enabled, total) {
  const countLabel = document.getElementById('wiz-patches-count-label');
  const repoLabel = document.getElementById('wiz-patches-repo-label');
  const owner = state.wizardPatches.selectedRepoOwner || 'all';
  if (countLabel) countLabel.textContent = `${enabled} of ${total} patches enabled`;
  if (repoLabel) repoLabel.textContent = owner === 'all' ? '[All Repositories]' : `[${owner}]`;
}

function toggleWizardPatch(owner, patchName, isChecked) {
  const repoData = state.wizardPatches.repoPatches[owner];
  if (!repoData) return;
  const p = repoData.patches.find(x => x.name === patchName);
  if (p) {
    p.enabled = isChecked;
    const { totalCount, enabledCount } = getFilteredPatches(true);
    updateWizardPatchCounts(enabledCount, totalCount);
  }
}

function setWizardPatchesDefault() {
  const owner = state.wizardPatches.selectedRepoOwner || 'all';
  const targets = (owner === 'all')
    ? Object.values(state.wizardPatches.repoPatches)
    : [state.wizardPatches.repoPatches[owner]].filter(Boolean);

  targets.forEach(rd => {
    (rd.patches || []).forEach(p => {
      p.enabled = p.defaultEnabled;
    });
  });
  renderWizardPatchesList();
  showToast('Restored official default patch selections', 'info');
}

function setWizardPatchesAll(enabled) {
  const owner = state.wizardPatches.selectedRepoOwner || 'all';
  const targets = (owner === 'all')
    ? Object.values(state.wizardPatches.repoPatches)
    : [state.wizardPatches.repoPatches[owner]].filter(Boolean);

  targets.forEach(rd => {
    (rd.patches || []).forEach(p => {
      p.enabled = enabled;
    });
  });
  renderWizardPatchesList();
}

// ---------------------------------------------------------------------------
// Application Configuration & Patch Editor Modal
// ---------------------------------------------------------------------------
async function openEditAppModal(pkg) {
  if (!morpheCatalogData) {
    try {
      await fetchMorpheCatalog();
    } catch (e) {
      console.warn('Could not fetch morphe catalog:', e);
    }
  }

  const app = state.configuredApps.find(a => (a.package || a.package_name) === pkg) || { package: pkg };
  const catalogItem = (morpheCatalogData && morpheCatalogData[pkg]) ? morpheCatalogData[pkg] : null;
  const friendlyName = catalogItem?.name || getFallbackAppName(pkg);
  const iconUrl = catalogItem?.icon_url;

  const titleEl = document.getElementById('edit-app-title');
  const pkgEl = document.getElementById('edit-app-pkg');
  const avatarEl = document.getElementById('edit-app-avatar');

  if (titleEl) titleEl.textContent = `Configure ${friendlyName}`;
  if (pkgEl) pkgEl.textContent = pkg;
  if (avatarEl) {
    avatarEl.innerHTML = iconUrl
      ? `<img src="${iconUrl}" style="width: 100%; height: 100%; object-fit: cover;" onerror="this.onerror=null; this.parentElement.innerHTML='<span class=\\'material-symbols-rounded\\'>android</span>'">`
      : `<span class="material-symbols-rounded">android</span>`;
  }

  const appRepos = app.repos || [];
  const activeReposSet = new Set(appRepos);

  state.editApp = {
    package: pkg,
    appName: friendlyName,
    iconUrl: iconUrl,
    repos: activeReposSet,
    initialRepos: new Set(appRepos),
    initialAliases: JSON.stringify(app.patch_aliases || []),
    aliases: JSON.parse(JSON.stringify(app.patch_aliases || [])),
    activeTab: 'repos',
    repoPatches: {},
    selectedRepoOwner: 'all',
    patchFilter: 'all',
    scopeFilter: 'all',
    patchSearch: '',
  };

  switchEditTab('repos');
  renderEditReposTab();
  renderEditRulesTab();

  document.getElementById('modal-edit-app')?.classList.add('open');

  // Pre-load patches in background for Tab 2 & Aliases in Tab 3
  try {
    await loadEditAppPatches();
  } catch (e) {
    console.error('Error loading patches for edit modal:', e);
  }
  renderEditRulesTab();
}

function closeEditAppModal() {
  document.getElementById('modal-edit-app')?.classList.remove('open');
}

function switchEditTab(tabName) {
  state.editApp.activeTab = tabName;

  document.querySelectorAll('#modal-edit-app .modal-tab-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.editTab === tabName);
  });

  document.getElementById('edit-tab-content-repos')?.classList.toggle('hidden', tabName !== 'repos');
  document.getElementById('edit-tab-content-patches')?.classList.toggle('hidden', tabName !== 'patches');
  document.getElementById('edit-tab-content-rules')?.classList.toggle('hidden', tabName !== 'rules');

  if (tabName === 'patches') {
    const currentActiveRepos = state.editApp.repos.size > 0 ? Array.from(state.editApp.repos) : state.globalRepos;
    const currentOwners = currentActiveRepos.map(extractRepoOwner);
    const existingLoadedOwners = Object.keys(state.editApp.repoPatches);
    const needsReload = currentOwners.some(o => !existingLoadedOwners.includes(o)) ||
                        existingLoadedOwners.some(o => !currentOwners.includes(o));

    if (needsReload || existingLoadedOwners.length === 0) {
      loadEditAppPatches();
    } else {
      renderEditPatchesList();
    }
  } else if (tabName === 'rules') {
    renderEditRulesTab();
  }
}

function renderEditReposTab() {
  const pkg = state.editApp.package;
  const list = document.getElementById('edit-suggested-repos-list');
  if (!list) return;

  list.innerHTML = '';

  const catalogItem = (morpheCatalogData && morpheCatalogData[pkg]) ? morpheCatalogData[pkg] : null;
  const suggested = (catalogItem?.repo_details && catalogItem.repo_details.length > 0)
    ? [...catalogItem.repo_details]
    : [];

  // Guarantee official Morphe repo is suggested for all Morphe default apps
  if (typeof MORPHE_DEFAULT_APPS !== 'undefined' && Object.keys(MORPHE_DEFAULT_APPS).includes(pkg)) {
    if (!suggested.some(r => r.repo_url === MORPHE_OFFICIAL_REPO_URL)) {
      suggested.unshift({
        repo_url: MORPHE_OFFICIAL_REPO_URL,
        name: 'Morphe',
        source: 'github',
        patch_count: MORPHE_DEFAULT_APPS[pkg]?.patch_count || 'Default',
      });
    }
  }
  const addedUrls = new Set();
  const suggestedInputs = [];
  const globalInputs = [];

  // Update master toolbar count badges
  const suggestedCountBadge = document.getElementById('edit-suggested-count-badge');
  if (suggestedCountBadge) suggestedCountBadge.textContent = suggested.length;

  // 1. Recommended Repositories Section
  const suggestedSection = document.createElement('div');
  suggestedSection.style.marginBottom = '14px';

  const suggestedHeader = document.createElement('div');
  suggestedHeader.style.cssText = 'display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;';
  suggestedHeader.innerHTML = `
    <div style="font-size: 0.8125rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase;">Recommended Repositories (${suggested.length})</div>
  `;
  suggestedSection.appendChild(suggestedHeader);

  const suggestedWrap = document.createElement('div');
  suggestedWrap.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';

  if (suggested.length > 0) {
    suggested.forEach(r => {
      addedUrls.add(r.repo_url);
      const isChecked = state.editApp.repos.has(r.repo_url);
      const el = createEditRepoCheckboxElement(r.repo_url, cleanRepoSlug(r.repo_url), r.patch_count ? `${r.patch_count} patches` : 'Compatible', isChecked, () => {
        updateEditMasterCheckboxes();
      });
      suggestedInputs.push(el.querySelector('input'));
      suggestedWrap.appendChild(el);
    });
  } else {
    const emptyNote = document.createElement('p');
    emptyNote.style.cssText = 'color: var(--text-muted); font-size: 0.8125rem; margin-bottom: 4px;';
    emptyNote.textContent = 'No package-specific suggested repos in Morphe catalog for this app.';
    suggestedWrap.appendChild(emptyNote);
  }
  suggestedSection.appendChild(suggestedWrap);
  list.appendChild(suggestedSection);

  // 2. Other Available Repositories Section
  const globalSection = document.createElement('div');
  globalSection.style.marginBottom = '14px';

  const globalEligible = state.globalRepos.filter(gUrl => !addedUrls.has(gUrl));
  const globalCountBadge = document.getElementById('edit-global-count-badge');
  if (globalCountBadge) globalCountBadge.textContent = globalEligible.length;

  const globalHeader = document.createElement('div');
  globalHeader.style.cssText = 'display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;';
  globalHeader.innerHTML = `
    <div style="font-size: 0.8125rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase;">Other Available Repositories (${globalEligible.length})</div>
  `;
  globalSection.appendChild(globalHeader);

  const globalWrap = document.createElement('div');
  globalWrap.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';

  globalEligible.forEach(gUrl => {
    addedUrls.add(gUrl);
    const isChecked = state.editApp.repos.has(gUrl);
    const item = createEditRepoCheckboxElement(gUrl, cleanRepoSlug(gUrl), 'Repository', isChecked, () => {
      updateEditMasterCheckboxes();
    });
    globalInputs.push(item.querySelector('input'));
    globalWrap.appendChild(item);
  });
  globalSection.appendChild(globalWrap);
  list.appendChild(globalSection);

  // 3. Custom repos already in repos
  const customSection = document.createElement('div');
  const customWrap = document.createElement('div');
  customWrap.style.cssText = 'display: flex; flex-direction: column; gap: 8px;';
  let hasCustom = false;

  state.editApp.repos.forEach(rUrl => {
    if (!addedUrls.has(rUrl)) {
      hasCustom = true;
      addedUrls.add(rUrl);
      const item = createEditRepoCheckboxElement(rUrl, cleanRepoSlug(rUrl), 'Custom', true, () => {
        updateEditMasterCheckboxes();
      });
      customWrap.appendChild(item);
    }
  });

  if (hasCustom) {
    customSection.innerHTML = `<div style="font-size: 0.8125rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase; margin-bottom: 8px;">Custom Repositories</div>`;
    customSection.appendChild(customWrap);
    list.appendChild(customSection);
  }

  function updateEditMasterCheckboxes() {
    const masterSuggested = document.getElementById('edit-toggle-all-suggested');
    if (masterSuggested) {
      if (suggestedInputs.length === 0) {
        masterSuggested.checked = false;
        masterSuggested.disabled = true;
        masterSuggested.indeterminate = false;
      } else {
        masterSuggested.disabled = false;
        const checkedCount = suggestedInputs.filter(i => i.checked).length;
        masterSuggested.checked = checkedCount === suggestedInputs.length;
        masterSuggested.indeterminate = checkedCount > 0 && checkedCount < suggestedInputs.length;
      }
    }

    const masterGlobal = document.getElementById('edit-toggle-all-global');
    if (masterGlobal) {
      if (globalInputs.length === 0) {
        masterGlobal.checked = false;
        masterGlobal.disabled = true;
        masterGlobal.indeterminate = false;
      } else {
        masterGlobal.disabled = false;
        const checkedCount = globalInputs.filter(i => i.checked).length;
        masterGlobal.checked = checkedCount === globalInputs.length;
        masterGlobal.indeterminate = checkedCount > 0 && checkedCount < globalInputs.length;
      }
    }
  }

  updateEditMasterCheckboxes();

  const masterSuggested = document.getElementById('edit-toggle-all-suggested');
  if (masterSuggested) {
    masterSuggested.onchange = (e) => {
      const isChecked = e.target.checked;
      suggested.forEach(r => {
        if (isChecked) state.editApp.repos.add(r.repo_url);
        else state.editApp.repos.delete(r.repo_url);
      });
      suggestedInputs.forEach(i => { i.checked = isChecked; });
      updateEditMasterCheckboxes();
    };
  }

  const masterGlobal = document.getElementById('edit-toggle-all-global');
  if (masterGlobal) {
    masterGlobal.onchange = (e) => {
      const isChecked = e.target.checked;
      globalEligible.forEach(gUrl => {
        if (isChecked) state.editApp.repos.add(gUrl);
        else state.editApp.repos.delete(gUrl);
      });
      globalInputs.forEach(i => { i.checked = isChecked; });
      updateEditMasterCheckboxes();
    };
  }
}

function addEditSuggestedRepoItem(repo, isChecked = true) {
  const list = document.getElementById('edit-suggested-repos-list');
  if (!list) return;
  const slug = cleanRepoSlug(repo.repo_url);
  const el = createEditRepoCheckboxElement(repo.repo_url, slug, repo.patch_count || 'Custom', isChecked);
  list.appendChild(el);
  if (isChecked) {
    state.editApp.repos.add(repo.repo_url);
  }
}

function createEditRepoCheckboxElement(url, label, badgeText, isChecked = false, onToggleChange = null) {
  const div = document.createElement('label');
  div.style.cssText = 'display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--bg-surface-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); cursor: pointer;';

  div.innerHTML = `
    <div style="display: flex; align-items: center; gap: 8px;">
      <input type="checkbox" value="${url}" ${isChecked ? 'checked' : ''}>
      <span style="font-weight: 600; font-size: 0.875rem;">${escapeHtml(label)}</span>
      <a href="${escapeHtml(formatRepoUrl(url))}" target="_blank" rel="noopener noreferrer" class="repo-row-link" title="Open repository in new tab" onclick="event.stopPropagation()">
        <span class="material-symbols-rounded" style="font-size: 14px;">open_in_new</span>
      </a>
    </div>
    <span class="badge badge-info">${badgeText}</span>
  `;

  div.querySelector('input').addEventListener('change', (e) => {
    if (e.target.checked) {
      state.editApp.repos.add(url);
    } else {
      state.editApp.repos.delete(url);
    }
    if (typeof onToggleChange === 'function') onToggleChange();
  });

  return div;
}


function renderEditRulesTab() {
  // Aliases
  const aliasContainer = document.getElementById('edit-aliases-list');
  if (!aliasContainer) return;

  const existingRows = aliasContainer.querySelectorAll('.alias-mapping-row');
  let itemsToRender = [];

  if (existingRows.length > 0) {
    existingRows.forEach(row => {
      const name = (row.querySelector('.alias-name')?.value || '').trim();
      const selectedPatches = [];
      row.querySelectorAll('.alias-repo-col').forEach(col => {
        const select = col.querySelector('.alias-repo-select');
        const custom = col.querySelector('.alias-repo-custom');
        let val = select ? select.value : '';
        if (val === '__custom__') val = (custom?.value || '').trim();
        if (val && !selectedPatches.includes(val)) selectedPatches.push(val);
      });
      if (name || selectedPatches.length > 0) {
        itemsToRender.push({ name, aliases: selectedPatches });
      }
    });
  } else if (Array.isArray(state.editApp.aliases) && state.editApp.aliases.length > 0) {
    itemsToRender = state.editApp.aliases;
  }

  aliasContainer.innerHTML = '';
  itemsToRender.forEach(item => {
    addEditAliasRow(item);
  });
}

function addEditAliasRow(aliasItem = null) {
  renderAliasRow(false, aliasItem);
}

async function loadEditAppPatches() {
  const pkg = state.editApp.package;
  const listEl = document.getElementById('edit-patches-list');
  if (listEl) {
    listEl.innerHTML = `<div style="text-align: center; padding: 28px; color: var(--text-dim);"><span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> Loading options and patch catalog...</div>`;
  }

  const activeRepos = state.editApp.repos.size > 0 ? Array.from(state.editApp.repos) : state.globalRepos;

  let [existingOptions, catalog] = await Promise.all([
    fetchAppOptions(pkg),
    fetchMorpheCatalog()
  ]);

  // Ensure options files are generated via Morphe options-create workflow for any missing repos
  const missingRepos = activeRepos.filter(repoUrl => {
    const owner = extractRepoOwner(repoUrl);
    return !existingOptions.files || !existingOptions.files[owner];
  });

  if (missingRepos.length > 0) {
    try {
      const fetchRes = await fetch(`/api/options-fetch/${encodeURIComponent(pkg)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repos: missingRepos, force: false })
      });
      if (fetchRes.ok) {
        existingOptions = await fetchAppOptions(pkg);
      }
    } catch (e) {
      console.warn('Auto options-fetch in edit modal failed:', e);
    }
  }

  state.editApp.repoPatches = {};

  activeRepos.forEach(repoUrl => {
    const owner = extractRepoOwner(repoUrl);
    const savedPatches = existingOptions.files?.[owner]?.patches || {};
    const catPatches = catalog?.[pkg]?.patches_by_repo?.[repoUrl] || [];

    const patchList = [];
    const seenNames = new Set();

    catPatches.forEach(cp => {
      seenNames.add(cp.name);
      const scope = determinePatchScope(cp.name, cp);
      const isUniv = scope === 'universal' || cp.isUniversal === true;
      const isDefault = isUniv ? false : Boolean(cp.default === true || cp.use === true);
      const saved = savedPatches[cp.name];
      const isEnabled = (saved !== undefined && saved.enabled !== undefined) ? (saved.enabled === true) : isDefault;

      let initialOptions = {};
      if (Array.isArray(cp.options)) {
        cp.options.forEach(opt => {
          if (opt.key) initialOptions[opt.key] = opt.default ?? opt.value ?? '';
        });
      } else if (cp.options && typeof cp.options === 'object' && !Array.isArray(cp.options)) {
        initialOptions = JSON.parse(JSON.stringify(cp.options));
      }
      if (saved?.options && typeof saved.options === 'object' && !Array.isArray(saved.options)) {
        Object.assign(initialOptions, JSON.parse(JSON.stringify(saved.options)));
      }
      if (Object.keys(initialOptions).length === 0) {
        initialOptions = null;
      }

      patchList.push({
        name: cp.name,
        description: cp.description,
        enabled: isEnabled,
        defaultEnabled: isDefault,
        options: initialOptions,
        schemaOptions: Array.isArray(cp.options) ? cp.options : null,
        repoOwner: owner,
        repoUrl: repoUrl,
        scope: determinePatchScope(cp.name, cp),
      });
    });

    for (const [sName, sData] of Object.entries(savedPatches)) {
      if (!seenNames.has(sName)) {
        seenNames.add(sName);
        const isEnabled = (sData.enabled === true);
        patchList.push({
          name: sName,
          description: '(Saved patch)',
          enabled: isEnabled,
          defaultEnabled: false,
          options: (sData.options && typeof sData.options === 'object') ? JSON.parse(JSON.stringify(sData.options)) : null,
          schemaOptions: null,
          repoOwner: owner,
          repoUrl: repoUrl,
          scope: determinePatchScope(sName, sData),
        });
      }
    }

    state.editApp.repoPatches[owner] = {
      repoUrl: repoUrl,
      owner: owner,
      patches: patchList
    };
  });

  const owners = Object.keys(state.editApp.repoPatches);
  const select = document.getElementById('edit-patch-repo-filter');
  if (select) {
    select.innerHTML = `<option value="all">All Repositories (${owners.length})</option>` +
      owners.map(o => {
        const r = state.editApp.repoPatches[o];
        return `<option value="${o}">${o} (${cleanRepoSlug(r.repoUrl)})</option>`;
      }).join('');

    if (!state.editApp.selectedRepoOwner || (state.editApp.selectedRepoOwner !== 'all' && !owners.includes(state.editApp.selectedRepoOwner))) {
      state.editApp.selectedRepoOwner = 'all';
    }
    select.value = state.editApp.selectedRepoOwner;
  }

  renderEditPatchesList();
}

function renderEditPatchesList() {
  const container = document.getElementById('edit-patches-list');
  if (!container) return;

  const { patches, totalCount, enabledCount } = getFilteredPatches(false);

  if (totalCount === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 28px; background: var(--bg-surface-elevated); border-radius: var(--radius-md); border: 1px dashed var(--border-subtle);">
        <span class="material-symbols-rounded" style="font-size: 36px; color: var(--text-dim);">tune</span>
        <div style="font-weight: 600; margin-top: 8px;">No Patches Loaded Yet</div>
        <p style="color: var(--text-muted); font-size: 0.8125rem; margin-top: 4px;">
          Check server options or fetch them directly from the repository.
        </p>
        <button type="button" class="btn btn-primary btn-sm" style="margin-top: 12px;" onclick="handleSeeAvailablePatches(false)">
          <span class="material-symbols-rounded">visibility</span>
          <span>See Available Patches</span>
        </button>
      </div>
    `;
    updateEditPatchCounts(0, 0);
    return;
  }

  if (patches.length === 0) {
    container.innerHTML = `<p style="padding: 24px; text-align: center; color: var(--text-muted);">No patches match your filter.</p>`;
    updateEditPatchCounts(enabledCount, totalCount);
    return;
  }

  container.innerHTML = patches.map(patch => renderPatchCardHtml(patch, false)).join('');
  updateEditPatchCounts(enabledCount, totalCount);
}

function updateEditPatchCounts(enabled, total) {
  const countLabel = document.getElementById('edit-patch-count-label');
  const owner = state.editApp.selectedRepoOwner || 'all';
  if (countLabel) {
    const scopeSuffix = state.editApp.scopeFilter !== 'all' ? ` (${state.editApp.scopeFilter})` : '';
    const repoPrefix = owner !== 'all' ? `[${owner}] ` : '';
    countLabel.textContent = `${repoPrefix}${enabled} of ${total} patches enabled${scopeSuffix}`;
  }
}

function toggleEditPatch(owner, patchName, isChecked) {
  const repoData = state.editApp.repoPatches[owner];
  if (!repoData) return;
  const p = repoData.patches.find(x => x.name === patchName);
  if (p) {
    p.enabled = isChecked;
    const { totalCount, enabledCount } = getFilteredPatches(false);
    updateEditPatchCounts(enabledCount, totalCount);
  }
}

function setEditPatchesDefault() {
  const owner = state.editApp.selectedRepoOwner || 'all';
  const targets = (owner === 'all')
    ? Object.values(state.editApp.repoPatches)
    : [state.editApp.repoPatches[owner]].filter(Boolean);

  targets.forEach(rd => {
    (rd.patches || []).forEach(p => {
      p.enabled = p.defaultEnabled;
    });
  });
  renderEditPatchesList();
  showToast('Restored official default patch selections', 'info');
}

function setEditPatchesAll(enabled) {
  const owner = state.editApp.selectedRepoOwner || 'all';
  const targets = (owner === 'all')
    ? Object.values(state.editApp.repoPatches)
    : [state.editApp.repoPatches[owner]].filter(Boolean);

  targets.forEach(rd => {
    (rd.patches || []).forEach(p => {
      p.enabled = enabled;
    });
  });
  renderEditPatchesList();
}

async function saveEditAppModal() {
  const pkg = state.editApp.package;
  const btnSave = document.getElementById('btn-edit-save');
  if (btnSave) {
    btnSave.disabled = true;
    btnSave.innerHTML = `<span class="material-symbols-rounded" style="animation: spin 1s infinite linear;">sync</span> <span>Saving...</span>`;
  }

  try {
    // 1. Save options files for each loaded repo owner
    let savedOptionsCount = 0;
    for (const [owner, repoData] of Object.entries(state.editApp.repoPatches)) {
      if (!repoData.patches || repoData.patches.length === 0) continue;
      const patchesPayload = {};
      repoData.patches.forEach(p => {
        patchesPayload[p.name] = {
          enabled: p.enabled,
          ...(p.options ? { options: p.options } : {})
        };
      });

      const optRes = await fetch(`/api/options/${encodeURIComponent(pkg)}/${encodeURIComponent(owner)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          meta: { package: pkg, repo: repoData.repoUrl },
          patches: patchesPayload
        })
      });
      if (optRes.ok) savedOptionsCount++;
    }

    // 2. Only update config.yaml if Repositories or Aliases were modified
    const aliases = collectAliasData(false);
    const finalRepos = Array.from(state.editApp.repos || []);
    const initialRepos = Array.from(state.editApp.initialRepos || []);
    const initialAliasesStr = state.editApp.initialAliases || '[]';
    const currentAliasesStr = JSON.stringify(aliases);

    const reposChanged = finalRepos.length !== initialRepos.length ||
      !finalRepos.every(r => initialRepos.includes(r));
    const aliasesChanged = currentAliasesStr !== initialAliasesStr;

    let configUpdated = false;
    if (reposChanged || aliasesChanged) {
      const payload = {
        package: pkg,
        repos: finalRepos,
        patch_aliases: aliases,
      };

      const cfgRes = await fetch('/api/config/app', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!cfgRes.ok) {
        const err = await cfgRes.json();
        throw new Error(err.detail || 'Failed to update app config');
      }
      configUpdated = true;
    }

    const toastMsg = configUpdated
      ? `Saved configuration & patch options for ${state.editApp.appName}!`
      : `Saved patch options for ${state.editApp.appName}!`;

    showToast(toastMsg, 'success');
    closeEditAppModal();
    loadConfig();
    loadStatus();
  } catch (err) {
    showToast(`Error saving: ${err.message}`, 'error');
  } finally {
    if (btnSave) {
      btnSave.disabled = false;
      btnSave.innerHTML = `<span class="material-symbols-rounded">save</span> <span>Save Changes</span>`;
    }
  }
}

// ---------------------------------------------------------------------------
// Toast Notification Utility
// ---------------------------------------------------------------------------
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.style.cssText = `
    background: var(--bg-surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 12px 18px;
    box-shadow: var(--shadow-lg);
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--text-main);
    display: flex;
    align-items: center;
    gap: 10px;
    animation: toast-in 0.2s ease;
  `;

  let icon = 'info';
  let color = 'var(--primary-cyan)';
  if (type === 'success') { icon = 'check_circle'; color = 'var(--color-success)'; }
  if (type === 'error') { icon = 'error'; color = 'var(--color-danger)'; }
  if (type === 'warning') { icon = 'warning'; color = 'var(--color-warning)'; }

  toast.innerHTML = `
    <span class="material-symbols-rounded" style="color: ${color}; font-size: 18px;">${icon}</span>
    <span>${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    toast.style.transition = 'all 0.2s ease';
    setTimeout(() => toast.remove(), 200);
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Hash Routing & Deep Linking (#/files, #/files/<pkg>)
// ---------------------------------------------------------------------------
function handleHashRouting() {
  const hash = window.location.hash || '';
  if (hash.startsWith('#files') || hash.startsWith('#/files')) {
    const sub = hash.replace(/^#\/?files\/?/, '');
    switchTab('files', decodeURIComponent(sub));
  } else if (!hash && state.activeTab === 'files') {
    switchTab('apps');
  }
}

// ---------------------------------------------------------------------------
// Patched APK File Browser & Result Viewer Logic
// ---------------------------------------------------------------------------
function navigateToPackageFiles(pkg) {
  switchTab('files', pkg);
}

async function loadFiles(subpath = '') {
  state.currentFilesPath = subpath;
  const tbody = document.getElementById('files-table-body');
  const breadcrumbsEl = document.getElementById('files-breadcrumbs');
  const countsEl = document.getElementById('files-stat-counts');
  const sizeEl = document.getElementById('files-stat-size');

  if (tbody) {
    tbody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align: center; padding: 48px; color: var(--text-dim);">
          <span class="material-symbols-rounded" style="font-size: 28px; animation: spin 1s infinite linear;">sync</span>
          <div style="margin-top: 8px;">Loading directory contents...</div>
        </td>
      </tr>
    `;
  }

  // Update hash without page reload
  const cleanPath = subpath.replace(/^\/+|\/+$/g, '');
  const targetHash = cleanPath ? `#/files/${cleanPath}` : '#/files';
  if (window.location.hash !== targetHash) {
    history.replaceState(null, '', targetHash);
  }

  try {
    const res = await fetch(`/api/files?path=${encodeURIComponent(subpath)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    state.cachedFilesData = data;

    // Render Breadcrumbs
    if (breadcrumbsEl) {
      breadcrumbsEl.innerHTML = data.breadcrumbs.map((crumb, idx) => {
        const isLast = idx === data.breadcrumbs.length - 1;
        if (isLast) {
          return `<span style="font-weight: 600; color: var(--primary-cyan);">${escapeHtml(crumb.name)}</span>`;
        }
        return `
          <button type="button" class="btn-link" onclick="loadFiles('${escapeHtml(crumb.path)}')" style="background: none; border: none; padding: 0; color: var(--text-muted); cursor: pointer; font-family: inherit; font-size: inherit; text-decoration: underline;">
            ${escapeHtml(crumb.name)}
          </button>
          <span style="color: var(--text-dim);">/</span>
        `;
      }).join('');
    }

    // Render Rows
    if (tbody) {
      let rowsHtml = '';

      // Parent Directory row
      if (data.parent_path !== null) {
        rowsHtml += `
          <tr class="file-table-row" style="border-bottom: 1px solid var(--border-subtle); cursor: pointer;" onclick="loadFiles('${escapeHtml(data.parent_path)}')">
            <td style="padding: 10px 16px; width: 36px; text-align: center;">
              <span class="material-symbols-rounded" style="color: var(--primary-cyan); font-size: 20px; vertical-align: middle;">arrow_upward</span>
            </td>
            <td style="padding: 10px 16px; font-weight: 600; color: var(--text-main);">
              .. Parent Directory
            </td>
            <td style="padding: 10px 16px; color: var(--text-dim); font-size: 0.8125rem;">Directory</td>
            <td class="col-mtime" style="padding: 10px 16px; color: var(--text-dim); font-size: 0.8125rem;">-</td>
            <td style="padding: 10px 16px; text-align: right;">
              <button class="btn btn-secondary btn-sm" type="button" onclick="event.stopPropagation(); loadFiles('${escapeHtml(data.parent_path)}')">
                <span>Up</span>
              </button>
            </td>
          </tr>
        `;
      }

      // Directories
      for (const d of data.dirs) {
        rowsHtml += `
          <tr class="file-table-row" style="border-bottom: 1px solid var(--border-subtle); cursor: pointer;" onclick="loadFiles('${escapeHtml(d.path)}')">
            <td style="padding: 10px 16px; width: 36px; text-align: center;">
              <span class="material-symbols-rounded" style="color: var(--primary-cyan); font-size: 20px; vertical-align: middle;">folder</span>
            </td>
            <td style="padding: 10px 16px;">
              <span class="file-item-name" style="font-weight: 600; color: var(--text-main);">${escapeHtml(d.name)}/</span>
            </td>
            <td style="padding: 10px 16px; color: var(--text-muted); font-size: 0.8125rem;">
              <span class="badge" style="background: rgba(32, 207, 206, 0.1); color: var(--primary-cyan); font-size: 0.75rem;">
                ${d.item_count} ${d.item_count === 1 ? 'item' : 'items'}
              </span>
            </td>
            <td class="col-mtime" style="padding: 10px 16px; color: var(--text-dim); font-size: 0.8125rem; font-family: var(--font-mono);">${escapeHtml(d.mtime)}</td>
            <td style="padding: 10px 16px; text-align: right;">
              <button class="btn btn-secondary btn-sm" type="button" onclick="event.stopPropagation(); loadFiles('${escapeHtml(d.path)}')">
                <span>Open</span>
                <span class="material-symbols-rounded" style="font-size: 15px;">arrow_forward</span>
              </button>
            </td>
          </tr>
        `;
      }

      // Files
      for (const f of data.files) {
        let iconHtml = '<span class="material-symbols-rounded" style="color: var(--text-dim); font-size: 20px; vertical-align: middle;">description</span>';
        let badgeHtml = '';
        let actionButtonsHtml = '';

        if (f.is_apk) {
          iconHtml = '<span class="material-symbols-rounded" style="color: var(--color-success); font-size: 20px; vertical-align: middle;">android</span>';
          badgeHtml = '<span class="badge badge-success" style="font-size: 0.75rem; margin-left: 6px;">APK</span>';
          actionButtonsHtml = `
            <a href="${f.url}" download="${escapeHtml(f.name)}" class="btn btn-primary btn-sm" style="display: inline-flex; align-items: center; gap: 4px;" title="Download patched APK">
              <span class="material-symbols-rounded" style="font-size: 15px;">download</span>
              <span class="file-action-text">Download APK</span>
            </a>
          `;
        } else if (f.is_result_json) {
          iconHtml = '<span class="material-symbols-rounded" style="color: var(--primary-cyan); font-size: 20px; vertical-align: middle;">analytics</span>';
          actionButtonsHtml = `
            <div style="display: inline-flex; gap: 6px; justify-content: flex-end;">
              <button onclick="viewResultJson('${f.url}', '${escapeHtml(f.name)}', '${escapeHtml(data.current_path)}')" class="btn btn-primary btn-sm" type="button" style="display: inline-flex; align-items: center; gap: 4px;" title="View patch audit results">
                <span class="material-symbols-rounded" style="font-size: 15px;">visibility</span>
                <span class="file-action-text">View</span>
              </button>
              <a href="${f.url}?download=1" download="${escapeHtml(f.name)}" class="btn btn-secondary btn-sm" style="display: inline-flex; align-items: center; gap: 4px;" title="Download raw result.json">
                <span class="material-symbols-rounded" style="font-size: 15px;">download</span>
                <span class="file-action-text">Download</span>
              </a>
            </div>
          `;
        } else {
          actionButtonsHtml = `
            <a href="${f.url}?download=1" download="${escapeHtml(f.name)}" class="btn btn-secondary btn-sm" style="display: inline-flex; align-items: center; gap: 4px;">
              <span class="material-symbols-rounded" style="font-size: 15px;">download</span>
              <span class="file-action-text">Download</span>
            </a>
          `;
        }

        rowsHtml += `
          <tr class="file-table-row" style="border-bottom: 1px solid var(--border-subtle);">
            <td style="padding: 10px 16px; width: 36px; text-align: center;">
              ${iconHtml}
            </td>
            <td style="padding: 10px 16px;">
              <span class="file-item-name" style="font-weight: 500; color: var(--text-main);">${escapeHtml(f.name)}</span>
              ${badgeHtml}
            </td>
            <td style="padding: 10px 16px; color: var(--text-muted); font-size: 0.8125rem; font-family: var(--font-mono);">${escapeHtml(f.size)}</td>
            <td class="col-mtime" style="padding: 10px 16px; color: var(--text-dim); font-size: 0.8125rem; font-family: var(--font-mono);">${escapeHtml(f.mtime)}</td>
            <td style="padding: 10px 16px; text-align: right;">
              ${actionButtonsHtml}
            </td>
          </tr>
        `;
      }

      if (data.dirs.length === 0 && data.files.length === 0 && data.parent_path === null) {
        rowsHtml = `
          <tr>
            <td colspan="5" style="text-align: center; padding: 48px; color: var(--text-dim);">
              <span class="material-symbols-rounded" style="font-size: 32px; color: var(--text-dim);">folder_off</span>
              <p style="margin-top: 8px;">No files or directories found in apps/patched/.</p>
            </td>
          </tr>
        `;
      } else if (data.dirs.length === 0 && data.files.length === 0) {
        rowsHtml += `
          <tr>
            <td colspan="5" style="text-align: center; padding: 32px; color: var(--text-dim);">
              This directory is empty.
            </td>
          </tr>
        `;
      }

      tbody.innerHTML = rowsHtml;
    }

    if (countsEl) {
      countsEl.textContent = `Showing ${data.dirs.length} ${data.dirs.length === 1 ? 'directory' : 'directories'}, ${data.files.length} ${data.files.length === 1 ? 'file' : 'files'}`;
    }
    if (sizeEl) {
      sizeEl.textContent = `Total: ${data.formatted_total_bytes}`;
    }

    // Re-apply filter if user has typed in search box
    filterFilesTable();

  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="5" style="text-align: center; padding: 40px; color: var(--color-danger);">
            <span class="material-symbols-rounded" style="font-size: 28px;">error</span>
            <p style="margin-top: 8px;">Failed to load files: ${escapeHtml(err.message)}</p>
            <button class="btn btn-secondary btn-sm" onclick="loadFiles('${escapeHtml(subpath)}')" style="margin-top: 12px;">Retry</button>
          </td>
        </tr>
      `;
    }
    showToast(`Error reading directory: ${err.message}`, 'error');
  }
}

function filterFilesTable() {
  const query = (document.getElementById('files-filter-input')?.value || '').toLowerCase().trim();
  document.querySelectorAll('.file-table-row').forEach(row => {
    const text = row.querySelector('.file-item-name')?.textContent.toLowerCase() || '';
    if (!query || text.includes(query)) {
      row.style.display = '';
    } else {
      row.style.display = 'none';
    }
  });
}

async function viewResultJson(fileUrl, fileName, contextPath) {
  const modal = document.getElementById('modal-result-viewer');
  if (!modal) return;

  // Open modal in loading state
  modal.classList.add('open');
  switchResultModalTab('summary');

  document.getElementById('res-meta-pkg').textContent = 'Loading...';
  document.getElementById('res-meta-ver').textContent = 'Loading...';
  document.getElementById('res-meta-status').innerHTML = '<span class="badge badge-info">Loading</span>';
  document.getElementById('res-modal-badge').className = 'badge badge-info';
  document.getElementById('res-modal-badge').textContent = 'Loading';
  document.getElementById('res-steps-container').innerHTML = '<span style="color: var(--text-dim);">Loading steps...</span>';
  document.getElementById('res-applied-list').innerHTML = '';
  document.getElementById('res-applied-count').textContent = '0';
  document.getElementById('res-failed-list').innerHTML = '';
  document.getElementById('res-failed-count').textContent = '0';
  document.getElementById('res-failed-wrapper').style.display = 'none';
  document.getElementById('res-json-pre').textContent = 'Loading result.json...';

  const fullDisplayPath = contextPath ? `${contextPath}/${fileName}` : fileName;
  const filePathEl = document.getElementById('res-modal-filepath');
  if (filePathEl) {
    filePathEl.textContent = fullDisplayPath;
    filePathEl.title = fullDisplayPath;
  }

  const btnDownload = document.getElementById('btn-res-modal-download');
  if (btnDownload) {
    btnDownload.href = `${fileUrl}?download=1`;
    btnDownload.download = fileName || 'result.json';
  }

  try {
    const res = await fetch(fileUrl);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.currentResultJson = data;

    // Package & Version
    document.getElementById('res-meta-pkg').textContent = data.packageName || '-';
    document.getElementById('res-meta-ver').textContent = data.packageVersion ? `v${data.packageVersion}` : '-';

    // Status Determination
    const isSuccess = data.success !== false && (!data.failedPatches || data.failedPatches.length === 0);
    const hasFailures = data.failedPatches && data.failedPatches.length > 0;
    const hasApplied = data.appliedPatches && data.appliedPatches.length > 0;

    let statusText = 'SUCCESS';
    let statusClass = 'badge-success';
    if (!isSuccess) {
      if (hasApplied && hasFailures) {
        statusText = 'PARTIAL';
        statusClass = 'badge-warning';
      } else {
        statusText = 'FAILED';
        statusClass = 'badge-danger';
      }
    }

    const badgeHeader = document.getElementById('res-modal-badge');
    badgeHeader.className = `badge ${statusClass}`;
    badgeHeader.textContent = statusText;

    document.getElementById('res-meta-status').innerHTML = `
      <span class="badge ${statusClass}">${statusText}</span>
    `;

    // Pipeline Steps
    const steps = data.stepResults || data.patchingSteps || [];
    const stepsContainer = document.getElementById('res-steps-container');
    if (steps.length === 0) {
      stepsContainer.innerHTML = `<span style="color: var(--text-dim); font-size: 0.8125rem;">No explicit steps recorded.</span>`;
    } else {
      stepsContainer.innerHTML = steps.map(s => {
        const stepSuccess = s.success !== false;
        const icon = stepSuccess ? 'check_circle' : 'cancel';
        const color = stepSuccess ? 'var(--color-success)' : 'var(--color-danger)';
        const bg = stepSuccess ? 'rgba(16, 185, 129, 0.1)' : 'rgba(244, 63, 94, 0.1)';
        return `
          <div style="background: ${bg}; border: 1px solid ${color}; border-radius: var(--radius-sm); padding: 6px 12px; display: inline-flex; align-items: center; gap: 6px; font-size: 0.8125rem;">
            <span class="material-symbols-rounded" style="color: ${color}; font-size: 16px;">${icon}</span>
            <span style="font-weight: 600; color: var(--text-main); font-family: var(--font-mono);">${escapeHtml(s.step || 'STEP')}</span>
            ${s.message ? `<span style="font-size: 0.75rem; color: var(--text-dim);">— ${escapeHtml(s.message)}</span>` : ''}
          </div>
        `;
      }).join('');
    }

    // Applied Patches
    const applied = data.appliedPatches || [];
    document.getElementById('res-applied-count').textContent = applied.length;
    const appliedListEl = document.getElementById('res-applied-list');
    if (applied.length === 0) {
      appliedListEl.innerHTML = `<div style="color: var(--text-dim); font-size: 0.8125rem; padding: 8px 0;">No applied patches recorded.</div>`;
    } else {
      appliedListEl.innerHTML = applied.map(p => {
        const pName = typeof p === 'string' ? p : (p.name || 'Unknown Patch');
        const options = (typeof p === 'object' && p.options) ? p.options : null;
        let optsHtml = '';
        if (Array.isArray(options) && options.length > 0) {
          optsHtml = `<div style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px;">` +
            options.map(opt => `<span style="background: var(--bg-surface-elevated); border: 1px solid var(--border-subtle); padding: 1px 6px; border-radius: 4px; font-size: 0.6875rem; font-family: var(--font-mono); color: var(--text-muted);">${escapeHtml(opt.key)}: ${escapeHtml(String(opt.value))}</span>`).join('') +
            `</div>`;
        }
        return `
          <div class="drawer-patch-item" style="padding: 8px 12px; background: var(--bg-surface-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); margin-bottom: 6px;">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span class="material-symbols-rounded" style="font-size: 15px; color: var(--color-success); flex-shrink: 0;">check</span>
              <span style="font-weight: 500; font-size: 0.8125rem; color: var(--text-main);">${escapeHtml(pName)}</span>
            </div>
            ${optsHtml}
          </div>
        `;
      }).join('');
    }

    // Failed Patches
    const failed = data.failedPatches || [];
    document.getElementById('res-failed-count').textContent = failed.length;
    const failedWrapper = document.getElementById('res-failed-wrapper');
    const failedListEl = document.getElementById('res-failed-list');
    if (failed.length > 0) {
      failedWrapper.style.display = 'block';
      failedListEl.innerHTML = failed.map(p => {
        const pName = typeof p === 'object' && p.patch ? (p.patch.name || 'Unknown Patch') : (p.name || 'Unknown Patch');
        const reason = typeof p === 'object' ? (p.reason || p.error || p.message || '') : String(p);
        const err = cleanPatchErrorMessage(reason);
        const stackHtml = err.stack
          ? `<details class="stacktrace-details" style="margin-top: 6px;"><summary style="cursor: pointer; font-size: 0.75rem; color: var(--primary-cyan);">View stack trace ▾</summary><pre class="stacktrace-pre" style="font-family: var(--font-mono); font-size: 0.75rem; max-height: 200px; overflow: auto; padding: 8px; background: rgba(0,0,0,0.3); border-radius: 4px; margin-top: 4px; white-space: pre-wrap;">${escapeHtml(err.stack)}</pre></details>`
          : '';
        return `
          <div class="drawer-patch-item failed" style="padding: 8px 12px; background: rgba(244, 63, 94, 0.08); border: 1px solid rgba(244, 63, 94, 0.25); border-radius: var(--radius-sm); margin-bottom: 6px;">
            <div style="display: flex; align-items: flex-start; gap: 6px;">
              <span class="material-symbols-rounded" style="font-size: 15px; color: var(--color-danger); flex-shrink: 0; margin-top: 2px;">close</span>
              <div style="flex: 1;">
                <span style="font-weight: 600; font-size: 0.8125rem; color: var(--text-main);">${escapeHtml(pName)}</span>
                ${err.summary ? `<div style="font-size: 0.75rem; color: var(--color-danger); margin-top: 2px;">${escapeHtml(err.summary)}</div>` : ''}
                ${stackHtml}
              </div>
            </div>
          </div>
        `;
      }).join('');
    } else {
      failedWrapper.style.display = 'none';
      failedListEl.innerHTML = '';
    }

    // Pretty JSON
    document.getElementById('res-json-pre').textContent = JSON.stringify(data, null, 2);

  } catch (err) {
    document.getElementById('res-json-pre').textContent = `Failed to load ${fileName}: ${err.message}`;
    document.getElementById('res-meta-status').innerHTML = `<span class="badge badge-danger">Error Loading</span>`;
    showToast(`Error reading result.json: ${err.message}`, 'error');
  }
}

function switchResultModalTab(tabName) {
  document.querySelectorAll('#modal-result-viewer .modal-tab-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.resTab === tabName);
  });
  document.getElementById('res-tab-content-summary')?.classList.toggle('hidden', tabName !== 'summary');
  document.getElementById('res-tab-content-json')?.classList.toggle('hidden', tabName !== 'json');
}

function closeResultViewerModal() {
  document.getElementById('modal-result-viewer')?.classList.remove('open');
}

