#!/usr/bin/env bash
# Lite navigation; all operations remain in their existing domain helpers.
xm_home() {
    local choice
    while true; do
        xm_header 'Главная'
        printf '  Сервер: %s\n  Подписки: %b · локальный доступ\n\n' "$SERVER_IP" "$(get_sub_server_status)"
        printf '  [1] Mieru        %b\n  [2] MasterDNS    %b\n' "$(get_mieru_status)" "$(get_dns_status)"
        printf '  [3] qWDTT        %b\n' "$(get_wdtt_status)"
        printf '  [4] Пользователи и подписки\n  [5] Сеть и оптимизация\n  [6] Обновление и обслуживание\n  [0] Выход\n'
        read -r -p 'Раздел: ' choice || return
        case "$choice" in
            1) menu_mieru;; 2) menu_dns;; 3) menu_wdtt;;
            4) menu_subscriptions;; 5) xm_lite_network_menu;; 6) xm_lite_maintenance;;
            0) echo 'Меню закрыто. Службы продолжают работать.'; return;;
            *) echo 'Выберите номер из списка.';;
        esac
    done
}
xm_lite_network_menu() {
    local choice
    while true; do
        xm_header 'Сеть и оптимизация'
        printf '  [1] Адаптивный профиль BBR/fq, буферы, conntrack\n'
        printf '  [2] DNS системы\n'
        printf '  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1) xm_network_profile_menu;;
            2) menu_dns_system;;
            0) return;;
            *) echo 'Выберите номер из списка.';;
        esac
    done
}
xm_lite_maintenance() {
    local choice
    while true; do
        xm_header 'Обслуживание'
        echo '  Источник обновлений: lesovoi53/x-manager-lite'
        printf '  [1] Обновить Lite\n  [2] Создать резервную копию\n  [3] Восстановить резервную копию\n  [4] Пересканировать службы\n  [5] Перезапустить работающие службы Lite\n  [6] Запуск, остановка и автозапуск\n  [7] Ежедневная перезагрузка VPS\n  [8] Версии компонентов\n  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1) xm_release_update;;
            2) bash /usr/local/share/x-manager/distribution/install.sh --backup; xm_pause;;
            3) xm_backup_menu;; 4) rescan_and_adopt_protocols;;
            5) xm_confirm 'Перезапустить работающие службы Lite? Соединения будут прерваны.' && restart_all_services;;
            6) python3 "$(dirname "${BASH_SOURCE[0]}")/service-control.py" menu;;
            7) xm_reboot_menu;;
            8) python3 -c 'import json; d=json.load(open("/usr/local/share/x-manager/components.json")); print("Lite",d["release"]); [print(k+": "+v) for k,v in d["versions"].items()]'; xm_pause;;
            0) return;;
        esac
    done
}
