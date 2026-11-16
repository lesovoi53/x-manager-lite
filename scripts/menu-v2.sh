#!/usr/bin/env bash
# Presentation/navigation only. Opening a page must never change the firewall.
xm_service_menu_unit() {
    case "$1" in
        menu_mieru) echo mita.service;;
        menu_subscriptions) echo tuna-subscriptions.service;; menu_dns) echo masterdns.service;;
        menu_wdtt) echo wdtt.service;;
        *) return 1;;
    esac
}
xm_direct_service_action() {
    local action=$1 unit=$2 warning
    case "$action" in on) ;; off)
        warning="Выключить $unit постоянно? Настройки и ссылки сохранятся."
        [ "$unit" != fail2ban.service ] || warning+=' Это отключит защиту от перебора паролей.'
        xm_confirm "$warning" || return 0;; *) return 1;;
    esac
    python3 "$(dirname "${BASH_SOURCE[0]}")/service-control.py" "$action" "$unit" || {
        echo 'Операция не завершена; проверьте ошибку выше.' >&2; xm_pause; return 1;
    }
}

xm_print_service_controls() {
    local unit
    if unit=$(xm_service_menu_unit "$1"); then
        printf '  [90] Включить службу\n  [91] Выключить постоянно\n'
    fi
}
xm_handle_service_control() {
    local name=$1 choice=$2 unit action
    case "$name:$choice" in
        menu_dns:92) unit=masterdns.service; action=on;; menu_dns:93) unit=masterdns.service; action=off;;
        *:90|*:91)
            unit=$(xm_service_menu_unit "$name") || return 1
            [ "$choice" != 90 ] && action=off || action=on;;
        *) return 1;;
    esac
    xm_direct_service_action "$action" "$unit" || return 0
}

xm_choose_action() {
    local xm_name=$1 xm_target=$2 xm_file xm_title xm_section xm_row xm_action xm_label xm_pick xm_group
    local -a xm_sections=() xm_ids=() xm_labels=()
    xm_file="$(dirname "${BASH_SOURCE[0]}")/menu-actions.tsv"
    [ -r "$xm_file" ] || { echo 'Missing menu action catalog' >&2; return 1; }
    while IFS='|' read -r xm_row xm_title xm_section xm_action xm_label; do
        [ "$xm_row" = "$xm_name" ] || continue
        if [ "$xm_section" = 'Панель 3X-UI' ] && [ ! -f /etc/x-ui/x-ui.db ]; then continue; fi
        [[ "|${xm_sections[*]}|" = *"$xm_section"* ]] || xm_sections+=("$xm_section")
    done < "$xm_file"
    xm_title=$(awk -F'|' -v n="$xm_name" '$1==n {print $2; exit}' "$xm_file")
    while true; do
        xm_header "$xm_title"
        case "$xm_name" in
            menu_mieru) printf '  Служба: %b\n' "$(get_mieru_status)";;
            menu_subscriptions) printf '  Служба: %b\n  Внешние ссылки добавляются в подписку пользователя.\n' "$(get_sub_server_status)";;
        esac
        for xm_pick in "${!xm_sections[@]}"; do printf '  [%d] %s\n' "$((xm_pick+1))" "${xm_sections[xm_pick]}"; done
        xm_print_service_controls "$xm_name"
        printf '  [0] Назад\n'
        read -r -p 'Раздел: ' xm_pick || return 1
        if xm_handle_service_control "$xm_name" "$xm_pick"; then continue; fi
        [ "$xm_pick" != 0 ] || { printf -v "$xm_target" '%s' 0; return; }
        [[ "$xm_pick" =~ ^[1-9][0-9]*$ ]] && ((xm_pick<=${#xm_sections[@]})) || continue
        xm_group=${xm_sections[xm_pick-1]}
        xm_ids=(); xm_labels=()
        while IFS='|' read -r xm_row xm_title xm_section xm_action xm_label; do
            [ "$xm_row" = "$xm_name" ] && [ "$xm_section" = "$xm_group" ] || continue
            xm_ids+=("$xm_action"); xm_labels+=("$xm_label")
        done < "$xm_file"
        xm_header "$xm_group"
        for xm_pick in "${!xm_ids[@]}"; do printf '  [%d] %s\n' "$((xm_pick+1))" "${xm_labels[xm_pick]}"; done
        xm_print_service_controls "$xm_name"
        echo '  [0] Назад'
        read -r -p 'Действие: ' xm_pick || return 1
        if xm_handle_service_control "$xm_name" "$xm_pick"; then continue; fi
        [ "$xm_pick" != 0 ] || continue
        [[ "$xm_pick" =~ ^[1-9][0-9]*$ ]] && ((xm_pick<=${#xm_ids[@]})) || continue
        printf -v "$xm_target" '%s' "${xm_ids[xm_pick-1]}"
        return
    done
}
xm_pause() { read -r -p 'Enter — назад: ' _ || return 0; }
xm_confirm() {
    printf '\n%s\n  [1] Продолжить\n  [0] Отмена\n' "$1"
    local answer
    read -r -p 'Выбор: ' answer && [ "$answer" = 1 ]
}
xm_header() { clear; printf '\n  X-MANAGER LITE / %s\n  ────────────────────────────────────────────────\n' "$1"; }
xm_release_update() {
    xm_header 'Обновление Lite'
    python3 -c 'import json; d=json.load(open("/usr/local/share/x-manager/components.json")); print("Установленный выпуск:",d["release"]); print("Компоненты:", ", ".join(k+" "+v for k,v in d["versions"].items()))' || return 1
    echo 'Источник: lesovoi53/x-manager-lite. Версии компонентов закреплены выпуском.'
    echo 'Данные, настройки и автозапуск сохраняются; перезапускаются ранее работавшие службы.'
    xm_confirm 'Получить и установить последний опубликованный выпуск?' || return 1
    if bash /usr/local/share/x-manager/scripts/update-release.sh; then
        echo 'Обновление завершено. Открываю новую версию меню.'
        exec /usr/local/bin/x-manager
    else
        echo 'Обновление не завершено. Проверьте ошибку и результат восстановления выше.' >&2
        xm_pause
        return 1
    fi
}
xm_services_menu() {
    local choice
    while true; do
        xm_header 'Службы и туннели'
        printf '  [1] Mieru        %b\n  [2] MasterDNS    %b\n  [3] qWDTT        %b\n' "$(get_mieru_status)" "$(get_dns_status)" "$(get_wdtt_status)"
        printf '  [4] Включение, выключение и автозапуск\n  [0] Назад\n'
        read -r -p 'Служба: ' choice || return
        case "$choice" in
            1) menu_mieru;; 2) menu_dns;; 3) menu_wdtt;;
            4) python3 "$(dirname "${BASH_SOURCE[0]}")/service-control.py" menu;;
            0) return;; *) echo 'Выберите номер из списка.';;
        esac
    done
}
xm_xray_menu() {
    local choice
    while true; do
        xm_header 'Xray и маршрутизация'
        echo 'Поддерживаются standalone Xray и ядро под управлением панели.'
        echo 'Сохранённые шлюзы:'
        if [ -r /etc/x-manager/gateways.env ]; then
            grep -E '^XRAY_(SOCKS|REDIRECT|TPROXY)_PORT=[0-9]+$' /etc/x-manager/gateways.env
        else
            echo 'Пока не заданы.'
        fi
        printf '\n  [1] Обнаружить шлюзы в конфигурации Xray\n  [2] Проверить доступность сохранённых шлюзов\n  [3] Маршрутизация отдельных служб\n  [4] Справка для standalone Xray\n  [0] Назад\n'
        printf '  [90] Включить standalone Xray\n  [91] Выключить standalone Xray постоянно\n'
        printf '  [92] Включить ядро и панель 3X-UI\n  [93] Выключить ядро и панель 3X-UI постоянно\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            90) xm_direct_service_action on xray.service;; 91) xm_direct_service_action off xray.service;;
            92) xm_direct_service_action on x-ui.service;; 93) xm_direct_service_action off x-ui.service;;
            1) python3 /usr/local/share/x-manager/scripts/xray-discovery.py; xm_pause;;
            2) (
                set -a
                [ ! -r /etc/x-manager/gateways.env ] || . /etc/x-manager/gateways.env
                python3 /usr/local/share/x-manager/scripts/xray-discovery.py --verify && echo 'TCP-шлюзы доступны; SOCKS5 принял соединение без авторизации. UDP/маршрут проверяются отдельно.'
                ); xm_pause;;
            3) xm_services_menu;;
            4) printf '%s\n' 'При установке: XM_XRAY_CONFIG=/путь/config.json bash install.sh' 'Также поддерживается каталог конфигураций и явно заданные XRAY_*_PORT.' 'Шлюзы: SOCKS5 без авторизации, dokodemo-door REDIRECT и TPROXY tcp,udp; listen 127.0.0.1.' 'Конфигурация standalone Xray не перезаписывается. Порты 443 и 8443 исключены.' 'Если Xray не нужен: bash install.sh --direct'; xm_pause;;
            0) return;; *) echo 'Выберите номер из списка'; sleep 1;;
        esac
    done
}
xm_security_menu() {
    local choice
    while true; do
        xm_header 'Сеть и безопасность'
        printf '  [1] Фаервол и доступ к портам\n  [2] SSL-сертификаты\n  [3] Сеть и оптимизация (BBR/fq, DNS)\n  [0] Назад\n'
        read -r -p 'Раздел: ' choice || return
        case "$choice" in 1) menu_firewall;; 2) menu_ssl;; 3) xm_lite_network_menu;; 0) return;; *) echo 'Неверный выбор';; esac
    done
}
xm_network_has_snapshot() {
    [ -f /var/lib/x-manager/network-profile/snapshot.json ] && [ ! -L /var/lib/x-manager/network-profile/snapshot.json ]
}
xm_network_profile_menu() {
    local choice helper
    helper="$(dirname "${BASH_SOURCE[0]}")/network-profile.py"
    xm_header 'Адаптивный профиль сети'
    if ! python3 "$helper" plan --adaptive --human; then
        echo 'Перед применением будут проверены зависимости и конфликты.'
    fi
    printf '\n  [1] Сохранить профиль под управлением X-Manager\n'
    if xm_network_has_snapshot; then
        printf '  [2] Восстановить предыдущие настройки\n'
    else
        printf '  Откат недоступен: сохранённой копии нет.\n'
    fi
    printf '  [3] Подробности проверки\n  [0] Назад\n'
    read -r -p 'Действие: ' choice || return
    case "$choice" in
        1) xm_confirm 'Подготовить модули и применить показанные настройки с резервной копией?' &&
           python3 "$helper" prepare --adaptive --human && python3 "$helper" apply --adaptive --human;;
        2) xm_network_has_snapshot || { echo 'Откат недоступен: сохранённой копии нет.'; return 1; }
           xm_confirm 'Восстановить сохранённые значения профиля?' && python3 "$helper" rollback --human;;
        3) python3 "$helper" plan --adaptive --human --details;;
        0) return;;
        *) echo 'Выберите номер из списка.';;
    esac
    xm_pause
}
xm_diagnostics_menu() {
    local choice helper_dir
    helper_dir=$(dirname "${BASH_SOURCE[0]}")
    while true; do
        xm_header 'Диагностика'
        printf '  [1] Состояние служб\n  [2] Сеть и система\n  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1) view_global_status;;
            2) python3 "$helper_dir/network-diagnostics.py"; xm_pause;;
            0) return;;
            *) echo 'Выберите номер из списка.';;
        esac
    done
}
xm_backup_menu() {
    local choice backup
    local -a backups=()
    xm_header 'Восстановление'
    while IFS= read -r backup; do
        [ -f "$backup/state.json" ] || continue
        if python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1])).get("edition") != "lite")' "$backup/state.json"; then backups+=("$backup"); fi
    done < <(find /var/backups -mindepth 1 -maxdepth 1 -type d -name 'x-manager-*' | sort -r)
    if [ "${#backups[@]}" = 0 ]; then echo 'Резервных копий установщика нет.'; xm_pause; return; fi
    for choice in "${!backups[@]}"; do printf '  [%d] %s\n' "$((choice+1))" "${backups[choice]}"; done
    echo '  [0] Назад'
    read -r -p 'Копия: ' choice || return
    [[ "$choice" =~ ^[1-9][0-9]*$ ]] && ((choice<=${#backups[@]})) || return
    backup=${backups[choice-1]}
    xm_confirm 'Восстановить файлы, данные и состояние служб из выбранной копии? Более новые изменения будут заменены.' || return
    bash /usr/local/share/x-manager/distribution/install.sh --rollback "$backup" || {
        echo 'Восстановление не завершено; проверьте сообщения выше.' >&2; xm_pause; return 1;
    }
    exec /usr/local/bin/x-manager
}
xm_reboot_menu() {
    local choice reboot_time helper
    helper="$(dirname "${BASH_SOURCE[0]}")/reboot-schedule.py"
    while true; do
        xm_header 'Ежедневная перезагрузка VPS'
        python3 "$helper" status --human || return 1
        echo 'Перезагрузка прерывает все соединения. Во время установки запуск пропускается.'
        printf '  [1] Включить в 04:00 по Москве\n  [2] Выбрать время по Москве\n  [3] Выключить\n  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1|2)
                reboot_time=04:00
                if [ "$choice" = 2 ]; then
                    read -r -p 'Время ЧЧ:ММ (Enter — назад): ' reboot_time || return
                    [ -n "$reboot_time" ] || continue
                fi
                xm_confirm "Перезагружать VPS ежедневно в $reboot_time по Москве, с задержкой до 10 минут?" || continue
                python3 "$helper" configure --time "$reboot_time" --timezone Europe/Moscow --human;;
            3) python3 "$helper" disable --human;;
            0) return;;
            *) echo 'Выберите номер из списка.';;
        esac
        xm_pause
    done
}
xm_maintenance_menu() {
    local choice
    while true; do
        xm_header 'Обслуживание'
        printf '  [1] Обновить выпуск Lite\n  [2] Восстановить резервную копию\n  [3] Пересканировать и подхватить службы\n  [4] Перезапустить службы пакета\n  [5] Ежедневная перезагрузка VPS\n  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1) xm_release_update;; 2) xm_backup_menu;;
            3) xm_confirm 'Подхватить обнаруженные службы? Это может обновить служебные настройки.' && rescan_and_adopt_protocols;;
            4) xm_confirm 'Перезапуск прервёт текущие подключения.' && restart_all_services;;
            5) xm_reboot_menu;;
            0) return;; *) echo 'Неверный выбор';;
        esac
    done
}
xm_home() {
    local choice
    while true; do
        xm_header 'Главная'
        printf '  Сервер: %s\n  Подписки: %b\n' "$SERVER_IP" "$(get_sub_server_status)"
        printf '\n  [1] Пользователи и подписки TUNA\n  [2] Службы и туннели\n  [3] Xray и маршрутизация\n  [4] Сеть и безопасность\n  [5] Диагностика и состояние служб\n  [6] Обновление и восстановление\n  [7] Справка\n  [0] Выход\n\n'
        read -r -p 'Раздел: ' choice || return
        case "$choice" in
            1) menu_subscriptions;; 2) xm_services_menu;; 3) xm_xray_menu;; 4) xm_security_menu;;
            5) xm_diagnostics_menu;; 6) xm_maintenance_menu;;
            7) printf '%s\n' '0 — назад; изменения выполняются только выбранным действием.' 'Выход из меню не останавливает службы и не запускает обновление.' 'Состояние службы, включение в подписку и состояние теста на телефоне — разные статусы.' 'URL-test / Speedtest запускаются клиентом после явного подключения.' 'Все реквизиты сохраняются при обновлении. Секретные ссылки показываются отдельным действием.'; xm_pause;;
            0) echo 'Меню закрыто. Службы продолжают работать.'; return;;
            *) echo 'Выберите номер из списка'; sleep 1;;
        esac
    done
}
