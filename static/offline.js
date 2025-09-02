// offline.js - Offline functionality for Legal Time Tracker

class OfflineManager {
  constructor() {
    this.isOnline = navigator.onLine;
    this.pendingEntries = [];
    this.init();
  }

  init() {
    // Listen for online/offline events
    window.addEventListener('online', () => this.handleOnline());
    window.addEventListener('offline', () => this.handleOffline());
    
    // Check for pending entries on page load
    this.loadPendingEntries();
    this.updateUI();
    
    // Register background sync if supported
    if ('serviceWorker' in navigator && 'sync' in window.ServiceWorkerRegistration.prototype) {
      navigator.serviceWorker.ready.then(reg => {
        this.swRegistration = reg;
      });
    }
  }

  handleOnline() {
    console.log('🟢 Back online');
    this.isOnline = true;
    this.hideOfflineIndicator();
    this.syncPendingEntries();
  }

  handleOffline() {
    console.log('🔴 Gone offline');
    this.isOnline = false;
    this.showOfflineIndicator();
  }

  showOfflineIndicator() {
    let indicator = document.getElementById('offline-indicator');
    if (!indicator) {
      indicator = document.createElement('div');
      indicator.id = 'offline-indicator';
      indicator.className = 'offline-indicator';
      indicator.innerHTML = '📱 Offline - Your entries will be saved locally';
      document.body.appendChild(indicator);
    }
    indicator.classList.add('show');
  }

  hideOfflineIndicator() {
    const indicator = document.getElementById('offline-indicator');
    if (indicator) {
      indicator.classList.remove('show');
      setTimeout(() => indicator.remove(), 300);
    }
  }

  showSyncIndicator(count) {
    let indicator = document.getElementById('sync-indicator');
    if (!indicator) {
      indicator = document.createElement('div');
      indicator.id = 'sync-indicator';
      indicator.className = 'offline-indicator sync-pending';
      document.body.appendChild(indicator);
    }
    indicator.innerHTML = `🔄 Syncing ${count} entries...`;
    indicator.classList.add('show');
  }

  hideSyncIndicator() {
    const indicator = document.getElementById('sync-indicator');
    if (indicator) {
      indicator.classList.remove('show');
      setTimeout(() => indicator.remove(), 300);
    }
  }

  // Save entry to localStorage when offline
  saveOfflineEntry(entryData) {
    const timestamp = Date.now();
    const entry = {
      ...entryData,
      id: `offline_${timestamp}`,
      timestamp: timestamp,
      synced: false
    };
    
    this.pendingEntries.push(entry);
    localStorage.setItem('pendingEntries', JSON.stringify(this.pendingEntries));
    
    console.log('💾 Saved offline entry:', entry);
    this.showOfflineSuccess();
  }

  loadPendingEntries() {
    const stored = localStorage.getItem('pendingEntries');
    if (stored) {
      try {
        this.pendingEntries = JSON.parse(stored);
        console.log(`📋 Loaded ${this.pendingEntries.length} pending entries`);
      } catch (e) {
        console.error('Failed to parse pending entries:', e);
        this.pendingEntries = [];
      }
    }
  }

  async syncPendingEntries() {
    if (this.pendingEntries.length === 0) return;
    
    this.showSyncIndicator(this.pendingEntries.length);
    
    const successfulSyncs = [];
    
    for (const entry of this.pendingEntries) {
      if (entry.synced) continue;
      
      try {
        const response = await fetch('/api/quick-entry', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: JSON.stringify({
            client: entry.client,
            matter: entry.matter,
            hours: entry.hours,
            desc: entry.desc,
            date_of_work: entry.date_of_work
          })
        });
        
        if (response.ok) {
          entry.synced = true;
          successfulSyncs.push(entry);
          console.log('✅ Synced entry:', entry.id);
        } else {
          console.error('❌ Failed to sync entry:', entry.id, response.status);
        }
      } catch (error) {
        console.error('❌ Sync error for entry:', entry.id, error);
        break; // Stop syncing if there's a network error
      }
    }
    
    // Remove synced entries
    this.pendingEntries = this.pendingEntries.filter(entry => !entry.synced);
    localStorage.setItem('pendingEntries', JSON.stringify(this.pendingEntries));
    
    this.hideSyncIndicator();
    
    if (successfulSyncs.length > 0) {
      this.showSyncSuccess(successfulSyncs.length);
      // Refresh the entries page if we're on it
      if (window.location.pathname === '/entries') {
        setTimeout(() => window.location.reload(), 1000);
      }
    }
  }

  showOfflineSuccess() {
    this.showToast('💾 Entry saved offline - will sync when connected', 'info');
  }

  showSyncSuccess(count) {
    this.showToast(`✅ Synced ${count} offline entries`, 'success');
  }

  showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} toast-notification`;
    toast.innerHTML = message;
    toast.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 9999;
      max-width: 300px;
      animation: slideIn 0.3s ease;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    `;
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
      toast.style.animation = 'slideOut 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  updateUI() {
    // Update pending entries counter
    const counter = document.getElementById('pending-counter');
    if (counter) {
      const count = this.pendingEntries.filter(e => !e.synced).length;
      counter.textContent = count;
      counter.style.display = count > 0 ? 'inline' : 'none';
    }
    
    // Show/hide offline elements
    const offlineElements = document.querySelectorAll('.offline-only');
    const onlineElements = document.querySelectorAll('.online-only');
    
    offlineElements.forEach(el => {
      el.style.display = this.isOnline ? 'none' : 'block';
    });
    
    onlineElements.forEach(el => {
      el.style.display = this.isOnline ? 'block' : 'none';
    });
  }

  // Enhanced form submission with offline support
  enhanceForm(formElement) {
    formElement.addEventListener('submit', async (e) => {
      e.preventDefault();
      
      const formData = new FormData(formElement);
      const entryData = {
        client: formData.get('client'),
        matter: formData.get('matter'),
        date_of_work: formData.get('date_of_work') || new Date().toISOString().split('T')[0],
        hours: parseFloat(formData.get('hours') || '0'),
        timekeeper: formData.get('timekeeper'),
        desc: formData.get('desc')
      };
      
      if (this.isOnline) {
        // Try normal submission
        try {
          const response = await fetch(formElement.action, {
            method: 'POST',
            body: formData
          });
          
          if (response.ok) {
            window.location.href = response.url || '/entries';
          } else {
            throw new Error('Network error');
          }
        } catch (error) {
          // Fall back to offline save
          this.saveOfflineEntry(entryData);
        }
      } else {
        // Save offline immediately
        this.saveOfflineEntry(entryData);
        
        // Clear form
        formElement.reset();
        
        // Navigate to entries page
        window.location.href = '/entries';
      }
    });
  }
}

// Initialize offline manager
const offlineManager = new OfflineManager();

// Enhanced page load functionality
document.addEventListener('DOMContentLoaded', function() {
  
  // Enhance entry forms with offline support
  const entryForms = document.querySelectorAll('form[action*="save"], form[action*="entry"]');
  entryForms.forEach(form => {
    offlineManager.enhanceForm(form);
  });
  
  // Add manual sync button
  const syncButton = document.getElementById('manual-sync');
  if (syncButton) {
    syncButton.addEventListener('click', () => {
      offlineManager.syncPendingEntries();
    });
  }
  
  // Add pull-to-refresh for entries page
  if (window.location.pathname === '/entries') {
    addPullToRefresh();
  }
  
  // Add quick action buttons
  addQuickActions();
  
  // Initialize touch gestures
  initTouchGestures();
  
  // Check for updates
  checkForUpdates();
});

// Pull to refresh functionality
function addPullToRefresh() {
  let startY = 0;
  let currentY = 0;
  let pulling = false;
  
  const threshold = 80;
  const container = document.querySelector('.container');
  
  if (!container) return;
  
  container.addEventListener('touchstart', (e) => {
    if (window.scrollY === 0) {
      startY = e.touches[0].clientY;
      pulling = true;
    }
  });
  
  container.addEventListener('touchmove', (e) => {
    if (!pulling) return;
    
    currentY = e.touches[0].clientY;
    const diffY = currentY - startY;
    
    if (diffY > 0 && diffY < threshold * 2) {
      e.preventDefault();
      const progress = Math.min(diffY / threshold, 1);
      updatePullIndicator(progress);
    }
  });
  
  container.addEventListener('touchend', () => {
    if (!pulling) return;
    
    const diffY = currentY - startY;
    if (diffY > threshold) {
      triggerRefresh();
    }
    
    hidePullIndicator();
    pulling = false;
  });
}

function updatePullIndicator(progress) {
  let indicator = document.getElementById('pull-indicator');
  if (!indicator) {
    indicator = document.createElement('div');
    indicator.id = 'pull-indicator';
    indicator.innerHTML = '🔄 Pull to refresh';
    indicator.style.cssText = `
      position: fixed;
      top: -60px;
      left: 50%;
      transform: translateX(-50%);
      background: #007bff;
      color: white;
      padding: 10px 20px;
      border-radius: 20px;
      font-size: 14px;
      z-index: 1000;
      transition: top 0.3s ease;
    `;
    document.body.appendChild(indicator);
  }
  
  indicator.style.top = `${Math.min(progress * 60 - 60, 10)}px`;
}

function hidePullIndicator() {
  const indicator = document.getElementById('pull-indicator');
  if (indicator) {
    indicator.style.top = '-60px';
    setTimeout(() => indicator.remove(), 300);
  }
}

function triggerRefresh() {
  const indicator = document.getElementById('pull-indicator');
  if (indicator) {
    indicator.innerHTML = '🔄 Refreshing...';
  }
  
  setTimeout(() => {
    window.location.reload();
  }, 500);
}

// Quick action floating buttons
function addQuickActions() {
  if (document.querySelector('.fab')) return; // Already exists
  
  // Only show on entries page
  if (window.location.pathname !== '/entries') return;
  
  const fab = document.createElement('a');
  fab.href = '/entry';
  fab.className = 'fab';
  fab.innerHTML = '+';
  fab.title = 'New Entry';
  document.body.appendChild(fab);
}

// Touch gesture enhancements
function initTouchGestures() {
  // Add swipe-to-delete for entry rows
  const entryRows = document.querySelectorAll('.entry-row');
  
  entryRows.forEach(row => {
    let startX = 0;
    let currentX = 0;
    let swiping = false;
    
    row.addEventListener('touchstart', (e) => {
      startX = e.touches[0].clientX;
      swiping = true;
    });
    
    row.addEventListener('touchmove', (e) => {
      if (!swiping) return;
      
      currentX = e.touches[0].clientX;
      const diffX = startX - currentX;
      
      if (diffX > 0 && diffX < 100) {
        row.style.transform = `translateX(-${diffX}px)`;
        row.style.backgroundColor = diffX > 50 ? '#ffe6e6' : '';
      }
    });
    
    row.addEventListener('touchend', () => {
      if (!swiping) return;
      
      const diffX = startX - currentX;
      if (diffX > 50) {
        // Show delete confirmation
        if (confirm('Delete this entry?')) {
          deleteEntry(row.dataset.entryId);
        }
      }
      
      // Reset position
      row.style.transform = '';
      row.style.backgroundColor = '';
      swiping = false;
    });
  });
}

// Check for app updates
function checkForUpdates() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      // New service worker activated, show update notification
      showUpdateNotification();
    });
    
    // Check for updates every 30 minutes
    setInterval(checkForServiceWorkerUpdate, 30 * 60 * 1000);
  }
}

function checkForServiceWorkerUpdate() {
  navigator.serviceWorker.getRegistrations().then(registrations => {
    registrations.forEach(registration => {
      registration.update();
    });
  });
}

function showUpdateNotification() {
  const notification = document.createElement('div');
  notification.className = 'alert alert-info';
  notification.style.cssText = `
    position: fixed;
    top: 20px;
    left: 20px;
    right: 20px;
    z-index: 9999;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
  `;
  notification.innerHTML = `
    📱 App updated! 
    <button onclick="window.location.reload()" class="btn btn-sm btn-primary ml-2">
      Refresh Now
    </button>
    <button onclick="this.parentElement.remove()" class="btn btn-sm btn-secondary ml-1">
      Later
    </button>
  `;
  
  document.body.appendChild(notification);
  
  // Auto-hide after 10 seconds
  setTimeout(() => {
    if (notification.parentElement) {
      notification.remove();
    }
  }, 10000);
}

// Enhanced entry form with better mobile UX
function enhanceEntryForm() {
  const form = document.getElementById('entry-form');
  if (!form) return;
  
  // Auto-save draft to localStorage
  const inputs = form.querySelectorAll('input, select, textarea');
  inputs.forEach(input => {
    input.addEventListener('input', saveDraft);
    input.addEventListener('change', saveDraft);
  });
  
  // Load draft on page load
  loadDraft();
  
  // Add quick time buttons
  addQuickTimeButtons();
  
  // Add voice input for description (if supported)
  addVoiceInput();
}

function saveDraft() {
  const form = document.getElementById('entry-form');
  if (!form) return;
  
  const formData = new FormData(form);
  const draft = {};
  for (const [key, value] of formData.entries()) {
    draft[key] = value;
  }
  
  localStorage.setItem('entryDraft', JSON.stringify(draft));
}

function loadDraft() {
  const draft = localStorage.getItem('entryDraft');
  if (!draft) return;
  
  try {
    const data = JSON.parse(draft);
    Object.entries(data).forEach(([key, value]) => {
      const input = document.querySelector(`[name="${key}"]`);
      if (input && value) {
        input.value = value;
      }
    });
  } catch (e) {
    console.error('Failed to load draft:', e);
  }
}

function clearDraft() {
  localStorage.removeItem('entryDraft');
}

function addQuickTimeButtons() {
  const hoursInput = document.querySelector('[name="hours"]');
  if (!hoursInput) return;
  
  const quickTimes = [0.25, 0.5, 1.0, 2.0, 4.0];
  const container = document.createElement('div');
  container.className = 'quick-time-buttons mt-2';
  container.innerHTML = '<small class="text-muted d-block mb-2">Quick select:</small>';
  
  quickTimes.forEach(hours => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-outline-secondary btn-sm mr-1 mb-1';
    btn.textContent = `${hours}h`;
    btn.onclick = () => {
      hoursInput.value = hours;
      hoursInput.focus();
    };
    container.appendChild(btn);
  });
  
  hoursInput.parentElement.appendChild(container);
}

function addVoiceInput() {
  const descInput = document.querySelector('[name="desc"]');
  if (!descInput || !('webkitSpeechRecognition' in window)) return;
  
  const voiceBtn = document.createElement('button');
  voiceBtn.type = 'button';
  voiceBtn.className = 'btn btn-outline-primary btn-sm voice-btn';
  voiceBtn.innerHTML = '🎤';
  voiceBtn.title = 'Voice input';
  voiceBtn.style.cssText = `
    position: absolute;
    right: 10px;
    top: 50%;
    transform: translateY(-50%);
    border: none;
    background: transparent;
    color: #007bff;
    font-size: 16px;
  `;
  
  descInput.parentElement.style.position = 'relative';
  descInput.parentElement.appendChild(voiceBtn);
  
  voiceBtn.onclick = () => {
    const recognition = new webkitSpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';
    
    voiceBtn.innerHTML = '🔴';
    voiceBtn.disabled = true;
    
    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      descInput.value = (descInput.value + ' ' + transcript).trim();
      saveDraft();
    };
    
    recognition.onend = () => {
      voiceBtn.innerHTML = '🎤';
      voiceBtn.disabled = false;
    };
    
    recognition.onerror = () => {
      voiceBtn.innerHTML = '🎤';
      voiceBtn.disabled = false;
      offlineManager.showToast('Voice input failed', 'warning');
    };
    
    recognition.start();
  };
}

// Initialize enhanced functionality when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', enhanceEntryForm);
} else {
  enhanceEntryForm();
}

// Utility function for deleting entries
function deleteEntry(entryId) {
  if (!entryId) return;
  
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = '/entries/delete';
  
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = 'id';
  input.value = entryId;
  
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
}

// CSS animations
const style = document.createElement('style');
style.textContent = `
  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }
  
  @keyframes slideOut {
    from { transform: translateX(0); opacity: 1; }
    to { transform: translateX(100%); opacity: 0; }
  }
  
  .toast-notification {
    animation: slideIn 0.3s ease;
  }
`;
document.head.appendChild(style);