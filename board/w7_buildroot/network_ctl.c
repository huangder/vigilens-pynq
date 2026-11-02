/* Small explicit RAM-session link control for systems without ethtool.
 * Query: network_ctl eth0
 * Change advertisement: network_ctl eth0 --100-full
 */
#define _DEFAULT_SOURCE
#include <errno.h>
#include <linux/ethtool.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

static int read_settings(int fd, struct ifreq *ifr, struct ethtool_cmd *ec)
{
    memset(ec, 0, sizeof(*ec));
    ec->cmd = ETHTOOL_GSET;
    ifr->ifr_data = (void *)ec;
    return ioctl(fd, SIOCETHTOOL, ifr);
}

static void print_settings(const char *label, const struct ethtool_cmd *ec)
{
    printf("%s speed=%u duplex=%s autoneg=%u supported=0x%08x advertising=0x%08x partner=0x%08x\n",
           label, ethtool_cmd_speed(ec),
           ec->duplex == DUPLEX_FULL ? "full" : ec->duplex == DUPLEX_HALF ? "half" : "unknown",
           ec->autoneg, ec->supported, ec->advertising, ec->lp_advertising);
}

int main(int argc, char **argv)
{
    struct ifreq ifr;
    struct ethtool_cmd ec;
    int fd, change;
    if (argc < 2 || argc > 3 || strlen(argv[1]) >= IFNAMSIZ ||
        (argc == 3 && strcmp(argv[2], "--100-full") != 0)) {
        fprintf(stderr, "usage: %s interface [--100-full]\n", argv[0]);
        return 2;
    }
    change = argc == 3;
    memset(&ifr, 0, sizeof(ifr));
    memcpy(ifr.ifr_name, argv[1], strlen(argv[1]) + 1);
    fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    if (read_settings(fd, &ifr, &ec) < 0) {
        perror("ETHTOOL_GSET"); close(fd); return 1;
    }
    print_settings("LINK_BEFORE", &ec);
    if (change) {
        if (!(ec.supported & SUPPORTED_100baseT_Full) || !(ec.supported & SUPPORTED_Autoneg)) {
            fprintf(stderr, "100baseT/Full autoneg is not supported\n");
            close(fd); return 1;
        }
        /* Retain pause and media capabilities; remove every advertised speed
         * except 100baseT/Full. No force-speed or host network changes. */
        ec.advertising &= ADVERTISED_Pause | ADVERTISED_Asym_Pause |
                          ADVERTISED_TP | ADVERTISED_AUI | ADVERTISED_MII |
                          ADVERTISED_FIBRE | ADVERTISED_BNC;
        ec.advertising |= ADVERTISED_100baseT_Full | ADVERTISED_Autoneg;
        ec.autoneg = AUTONEG_ENABLE;
        ec.duplex = DUPLEX_FULL;
        ethtool_cmd_speed_set(&ec, SPEED_100);
        ec.cmd = ETHTOOL_SSET;
        ifr.ifr_data = (void *)&ec;
        if (ioctl(fd, SIOCETHTOOL, &ifr) < 0) {
            perror("ETHTOOL_SSET"); close(fd); return 1;
        }
        if (read_settings(fd, &ifr, &ec) < 0) {
            perror("ETHTOOL_GSET after SSET"); close(fd); return 1;
        }
        print_settings("LINK_AFTER_REQUEST", &ec);
        if (!(ec.advertising & ADVERTISED_100baseT_Full) ||
            (ec.advertising & (ADVERTISED_10baseT_Half | ADVERTISED_10baseT_Full |
                               ADVERTISED_100baseT_Half | ADVERTISED_1000baseT_Half |
                               ADVERTISED_1000baseT_Full))) {
            fprintf(stderr, "advertisement readback did not match request\n");
            close(fd); return 1;
        }
        puts("ADVERTISEMENT_100_FULL_SET; link negotiation is asynchronous, query again after link settles");
    }
    close(fd);
    return 0;
}
