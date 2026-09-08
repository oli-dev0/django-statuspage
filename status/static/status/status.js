(function () {
  const storageKey = "statusAppearance";
  const refreshIntervalMs = 5 * 60 * 1000;
  const uptimeBarHoverMs = 140;
  const allowedThemes = new Set(["light", "dark"]);
  const root = document.documentElement;
  const control = document.querySelector("[data-status-appearance-toggle]");
  const statusFooter = document.querySelector(".status-footer");
  const pageTopLink = document.querySelector("[data-page-top-link]");
  let shouldRefreshWhenVisible = false;
  let uptimeTooltip = null;
  let uptimeTooltipHideTimer = null;

  function getStoredTheme() {
    try {
      return window.localStorage.getItem(storageKey);
    } catch (error) {
      return null;
    }
  }

  function storeTheme(theme) {
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch (error) {
      return;
    }
  }

  function applyTheme(theme) {
    const selectedTheme = allowedThemes.has(theme) ? theme : "dark";
    root.setAttribute("data-status-theme", selectedTheme);
    if (control) {
      const input = control.querySelector(`input[value="${selectedTheme}"]`);
      if (input) {
        input.checked = true;
      }
    }
  }

  function createFormatter(options) {
    try {
      return new Intl.DateTimeFormat(navigator.languages || undefined, options);
    } catch (error) {
      return null;
    }
  }

  function applyLocalTimes() {
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!timeZone) {
      return;
    }
    const dateFormatter = createFormatter({
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone,
    });
    const dateTimeFormatter = createFormatter({
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone,
    });

    document.querySelectorAll("[data-local-datetime]").forEach(function (element) {
      const date = new Date(element.dataset.localDatetime);
      if (Number.isNaN(date.getTime())) {
        return;
      }

      const format = element.dataset.localFormat || "datetime";
      const dateText = formatDate(dateFormatter, date);

      if (format === "date-title") {
        if (dateText && element.dataset.statusTooltip) {
          const tooltip = element.dataset.statusTooltip;
          element.dataset.statusTooltipText = `${dateText}: ${tooltip}`.trim();
        }
        return;
      }

      const text = format === "date" ? dateText : formatDateTime(dateTimeFormatter, date);
      if (text) {
        element.textContent = text;
      }
    });
  }

  function formatDate(formatter, date) {
    if (!formatter) {
      return null;
    }

    const parts = formatter.formatToParts(date);
    const partValue = function (type) {
      const part = parts.find(function (item) {
        return item.type === type;
      });
      return part ? part.value : "";
    };
    const day = partValue("day");
    const month = partValue("month");
    const year = partValue("year");

    if (!day || !month || !year) {
      return formatter.format(date);
    }
    return `${day} ${month} ${year}`;
  }

  function formatDateTime(formatter, date) {
    if (!formatter) {
      return null;
    }

    const parts = formatter.formatToParts(date);
    const partValue = function (type) {
      const part = parts.find(function (item) {
        return item.type === type;
      });
      return part ? part.value : "";
    };
    const day = partValue("day");
    const month = partValue("month");
    const year = partValue("year");
    const hour = partValue("hour");
    const minute = partValue("minute");

    if (!day || !month || !year || !hour || !minute) {
      return formatter.format(date);
    }
    return `${day} ${month} ${year} at ${hour}:${minute}`;
  }

  function bindUptimeBarHover() {
    document.querySelectorAll(".uptime-bar").forEach(function (bar) {
      let hoverTimer = null;

      bar.addEventListener("pointerenter", function () {
        clearUptimeTooltipHideTimer();
        if (hoverTimer) {
          window.clearTimeout(hoverTimer);
        }

        bar.classList.add("is-hovered");
        showUptimeTooltip(bar);
      });

      bar.addEventListener("pointerleave", function () {
        hoverTimer = window.setTimeout(function () {
          bar.classList.remove("is-hovered");
          hoverTimer = null;
        }, uptimeBarHoverMs);
        scheduleUptimeTooltipHide();
      });
    });
  }

  function getUptimeTooltip() {
    if (uptimeTooltip) {
      return uptimeTooltip;
    }

    uptimeTooltip = document.createElement("div");
    uptimeTooltip.className = "uptime-tooltip";
    uptimeTooltip.setAttribute("role", "tooltip");
    uptimeTooltip.addEventListener("pointerenter", clearUptimeTooltipHideTimer);
    uptimeTooltip.addEventListener("pointerleave", scheduleUptimeTooltipHide);
    document.body.appendChild(uptimeTooltip);
    return uptimeTooltip;
  }

  function showUptimeTooltip(bar) {
    const tooltipText = bar.dataset.statusTooltipText || "";
    if (!tooltipText) {
      hideUptimeTooltip();
      return;
    }

    const tooltip = getUptimeTooltip();
    clearUptimeTooltipHideTimer();
    renderUptimeTooltipText(tooltip, tooltipText);
    tooltip.classList.add("is-visible");
    positionUptimeTooltip(bar, tooltip);
  }

  function renderUptimeTooltipText(tooltip, tooltipText) {
    const separatorIndex = tooltipText.indexOf(": ");
    if (separatorIndex === -1) {
      tooltip.textContent = tooltipText;
      return;
    }

    const dateText = tooltipText.slice(0, separatorIndex);
    const descriptionText = tooltipText.slice(separatorIndex + 2);
    tooltip.replaceChildren(
      createTooltipLine("uptime-tooltip__date", dateText),
      createTooltipLine("uptime-tooltip__description", descriptionText),
    );
  }

  function createTooltipLine(className, text) {
    const line = document.createElement("span");
    line.className = className;
    line.textContent = text;
    return line;
  }

  function scheduleUptimeTooltipHide() {
    clearUptimeTooltipHideTimer();
    uptimeTooltipHideTimer = window.setTimeout(function () {
      hideUptimeTooltip();
      uptimeTooltipHideTimer = null;
    }, uptimeBarHoverMs);
  }

  function clearUptimeTooltipHideTimer() {
    if (uptimeTooltipHideTimer) {
      window.clearTimeout(uptimeTooltipHideTimer);
      uptimeTooltipHideTimer = null;
    }
  }

  function hideUptimeTooltip() {
    if (uptimeTooltip) {
      uptimeTooltip.classList.remove("is-visible");
    }
  }

  function positionUptimeTooltip(bar, tooltip) {
    const barRect = bar.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const viewportPadding = 12;
    const top = Math.max(viewportPadding, barRect.top - tooltipRect.height - 10);
    const centeredLeft = barRect.left + barRect.width / 2 - tooltipRect.width / 2;
    const maxLeft = window.innerWidth - tooltipRect.width - viewportPadding;
    const left = Math.min(Math.max(viewportPadding, centeredLeft), maxLeft);

    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
  }

  function bindPageTopLink() {
    if (!pageTopLink) {
      return;
    }

    let isTicking = false;
    let isReturningToTop = false;
    let returnTargetTop = 0;
    const showAfter = 420;
    const defaultBottom = 24;
    const footerGap = 12;
    const reduceMotionMedia = window.matchMedia("(prefers-reduced-motion: reduce)");

    function updatePageTopLink() {
      let bottomOffset = defaultBottom;

      if (statusFooter) {
        const footerTop = statusFooter.getBoundingClientRect().top;
        if (footerTop < window.innerHeight) {
          bottomOffset = Math.max(defaultBottom, window.innerHeight - footerTop + footerGap);
        }
      }

      pageTopLink.style.setProperty("--page-top-link-bottom", `${bottomOffset}px`);
      if (isReturningToTop && window.scrollY <= returnTargetTop + 4) {
        isReturningToTop = false;
      }
      pageTopLink.classList.toggle("is-visible", isReturningToTop || window.scrollY > showAfter);
      isTicking = false;
    }

    function requestPageTopLinkUpdate() {
      if (isTicking) {
        return;
      }
      isTicking = true;
      window.requestAnimationFrame(updatePageTopLink);
    }

    updatePageTopLink();
    pageTopLink.addEventListener("click", function (event) {
      const targetId = pageTopLink.getAttribute("href");
      const target = targetId ? document.querySelector(targetId) : null;
      if (!target) {
        return;
      }

      event.preventDefault();
      returnTargetTop = target.getBoundingClientRect().top + window.scrollY;
      isReturningToTop = true;
      pageTopLink.classList.add("is-visible");
      target.scrollIntoView({
        behavior: reduceMotionMedia.matches ? "auto" : "smooth",
        block: "start",
      });
      requestPageTopLinkUpdate();
    });
    window.addEventListener("scroll", requestPageTopLinkUpdate, { passive: true });
    window.addEventListener("resize", requestPageTopLinkUpdate);
  }

  applyTheme(getStoredTheme() || "dark");
  applyLocalTimes();
  bindUptimeBarHover();
  bindPageTopLink();

  window.setTimeout(function () {
    if (document.visibilityState === "hidden") {
      shouldRefreshWhenVisible = true;
      return;
    }

    window.location.reload();
  }, refreshIntervalMs);

  document.addEventListener("visibilitychange", function () {
    if (shouldRefreshWhenVisible && document.visibilityState === "visible") {
      window.location.reload();
    }
  });

  if (control) {
    control.addEventListener("change", function (event) {
      if (!event.target.matches("input[name='status-appearance']")) {
        return;
      }
      applyTheme(event.target.value);
      storeTheme(event.target.value);
    });
  }
})();
