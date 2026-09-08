'use strict';

{
    function initMonitorOrder() {
        const table = document.querySelector('[data-status-monitor-table]');
        if (!table) {
            return;
        }

        function getRows() {
            return Array.from(table.querySelectorAll('[data-status-monitor-row]'));
        }

        function syncPositions() {
            const rows = getRows();
            const lastIndex = rows.length - 1;

            rows.forEach((row, index) => {
                const positionInput = row.querySelector('[data-status-monitor-position]');
                if (positionInput) {
                    positionInput.value = String(index + 1);
                }

                const upButton = row.querySelector('[data-status-monitor-move="up"]');
                const downButton = row.querySelector('[data-status-monitor-move="down"]');
                if (upButton) {
                    upButton.disabled = index === 0;
                }
                if (downButton) {
                    downButton.disabled = index === lastIndex;
                }
            });
        }

        function moveRow(row, direction) {
            if (direction === 'up' && row.previousElementSibling) {
                row.parentNode.insertBefore(row, row.previousElementSibling);
            }
            if (direction === 'down' && row.nextElementSibling) {
                row.parentNode.insertBefore(row.nextElementSibling, row);
            }
            syncPositions();
        }

        table.addEventListener('click', (event) => {
            const button = event.target.closest('[data-status-monitor-move]');
            if (!button) {
                return;
            }

            const row = button.closest('[data-status-monitor-row]');
            if (!row) {
                return;
            }

            moveRow(row, button.dataset.statusMonitorMove);
            button.focus();
        });

        syncPositions();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initMonitorOrder);
    } else {
        initMonitorOrder();
    }
}
