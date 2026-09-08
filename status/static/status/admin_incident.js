(function () {
  var enhancedSelectCounter = 0;

  function initStatusPageReload() {
    if (!window.location.pathname.endsWith('/add/')) {
      return;
    }

    var statusPageSelect = document.querySelector('select[name="status_page"]');
    if (!statusPageSelect) {
      return;
    }

    statusPageSelect.addEventListener('change', function () {
      var url = new URL(window.location.href);

      if (statusPageSelect.value) {
        url.searchParams.set('status_page', statusPageSelect.value);
      } else {
        url.searchParams.delete('status_page');
      }

      window.location.assign(url.toString());
    });
  }

  function closeEnhancedSelect(dropdown) {
    dropdown.classList.remove('is-open');
    var button = dropdown.querySelector('.admin-action-select__button');
    if (button) {
      button.setAttribute('aria-expanded', 'false');
    }
  }

  function bindClonedEnhancedSelect(select) {
    var dropdown = select.nextElementSibling;
    if (
      !dropdown
      || !dropdown.classList.contains('admin-action-select')
      || dropdown.dataset.statusIncidentBound === 'true'
    ) {
      return;
    }

    var button = dropdown.querySelector('.admin-action-select__button');
    var buttonText = dropdown.querySelector('.admin-action-select__button-text');
    var menu = dropdown.querySelector('.admin-action-select__menu');
    var optionButtons = Array.prototype.slice.call(dropdown.querySelectorAll('.admin-action-select__option'));

    if (!button || !buttonText || !menu || optionButtons.length === 0) {
      return;
    }

    enhancedSelectCounter += 1;
    menu.id = 'status-incident-inline-select-menu-' + enhancedSelectCounter;
    button.setAttribute('aria-controls', menu.id);
    dropdown.dataset.statusIncidentBound = 'true';

    function setOpen(isOpen) {
      dropdown.classList.toggle('is-open', isOpen);
      button.setAttribute('aria-expanded', String(isOpen));

      if (isOpen) {
        var selectedButton = optionButtons.find(function (optionButton) {
          return optionButton.dataset.value === select.value;
        });
        window.requestAnimationFrame(function () {
          (selectedButton || optionButtons[0]).focus();
        });
      }
    }

    function syncButtonLabel() {
      var selectedOption = select.selectedOptions[0] || select.options[0];
      buttonText.textContent = selectedOption ? selectedOption.textContent.trim() : '';

      optionButtons.forEach(function (optionButton) {
        var isSelected = optionButton.dataset.value === select.value;
        optionButton.classList.toggle('is-selected', isSelected);
        optionButton.setAttribute('aria-selected', String(isSelected));
      });
    }

    function chooseOption(optionButton) {
      select.value = optionButton.dataset.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      dropdown.classList.remove('has-error');
      syncButtonLabel();
      setOpen(false);
      button.focus();
    }

    button.addEventListener('click', function () {
      setOpen(!dropdown.classList.contains('is-open'));
    });

    button.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        setOpen(true);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setOpen(true);
        window.requestAnimationFrame(function () {
          optionButtons[optionButtons.length - 1].focus();
        });
      } else if (event.key === 'Escape') {
        setOpen(false);
      }
    });

    optionButtons.forEach(function (optionButton, index) {
      optionButton.addEventListener('pointerdown', function (event) {
        event.preventDefault();
        chooseOption(optionButton);
      });

      optionButton.addEventListener('click', function () {
        chooseOption(optionButton);
      });

      optionButton.addEventListener('keydown', function (event) {
        var lastIndex = optionButtons.length - 1;

        if (event.key === 'ArrowDown') {
          event.preventDefault();
          optionButtons[Math.min(index + 1, lastIndex)].focus();
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          optionButtons[Math.max(index - 1, 0)].focus();
        } else if (event.key === 'Home') {
          event.preventDefault();
          optionButtons[0].focus();
        } else if (event.key === 'End') {
          event.preventDefault();
          optionButtons[lastIndex].focus();
        } else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          chooseOption(optionButton);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          setOpen(false);
          button.focus();
        }
      });
    });

    select.addEventListener('change', syncButtonLabel);
    syncButtonLabel();
  }

  function bindClonedEnhancedSelects(root) {
    Array.prototype.forEach.call(root.querySelectorAll('select'), bindClonedEnhancedSelect);
  }

  function initInlineSelectRebinding() {
    document.addEventListener('formset:added', function (event) {
      bindClonedEnhancedSelects(event.target);
    });

    document.addEventListener('click', function (event) {
      Array.prototype.forEach.call(
        document.querySelectorAll('.admin-action-select[data-status-incident-bound="true"].is-open'),
        function (dropdown) {
          if (!dropdown.contains(event.target)) {
            closeEnhancedSelect(dropdown);
          }
        }
      );
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initStatusPageReload();
      initInlineSelectRebinding();
    });
  } else {
    initStatusPageReload();
    initInlineSelectRebinding();
  }
})();
