#!/usr/bin/env python3
"""Frozen Stage-5 fake-green corpus tests for ``self_heal_review``.

The corpus is an embedded, compressed JSON snapshot of the 129 candidate bodies
from the Stage-5 run summarized by ``BUGate-stage5/05_fakegreen_corpus.md`` (sha256
``dd5ff5fafde90262d1a3359f3008ec142812c7cd42ffaa419cd5963cdd6b54f4``).
Keeping the bodies here makes the test hermetic: it neither imports the external
Stage-5 worktree nor executes untrusted candidates during structural analysis.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zlib
from collections import Counter
from pathlib import Path


TESTS = Path(__file__).resolve().parent
ENGINE = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ENGINE / "scripts"))

from self_heal_review import (  # noqa: E402
    F_ANY_OF_EXCEPT,
    F_EXPECTED_TO_ACTUAL,
    F_LOCAL_SUBSTITUTE,
    F_NOT_SUCCESS,
    F_RECORDED_EXCEPTION,
    F_SKIP_XFAIL,
    F_TIMEOUT_INFLATED,
    F_VACUOUS_ASSERTION,
    FileChange,
    structural_review,
)
from test_self_healing_governance import (  # noqa: E402
    BASE_BROKEN,
    BASE_EXPECTED_VALUE,
    LOG_EXPECTED_VALUE,
    LOG_TEST_CODE,
)


_CORPUS_B85 = r"""
c-
rk<dvDuD68|c!i^EAhQ*fK+{z%V<Dz=(4>PKKHMT<BfR^&?BGsWd`N!vn@?|w78%a=sTqDV<}+86;Oid@e8=CL!guaBRPJTj+8Z;
k|YC#KEkJ_(rMp2^381`pIZIzF;VNT)nlz+FVZ)sVsO9*sg8FmDPsOamVI@atfB)_*@Zhbv?5;O#eWhlAVS2It19@x#f<orix8of
sh%p%qY@2M%>C9)<8?+UdTz^9=Z>USgyVpC+GG6D5z|c}HK46Nc!MPwkL8=9ovGV}|^YEXBkaSynQ<roV5+D-)*2L-
_Ae6Bx0%C&D1Y2HVUl69!OiTm#h=cm>A7*+TJ1yzub)S%LP*V<FyXswU|FBI258d9}D;bDsk}apOo$5o5Ls?Hae5#2ZE72p;IfPx
kQJ84)z*v>Xd0I=&14;rE`Rdw^VZVhWBEoVtn(HZd#<@vtn^_PZm?niJ+(*4>eAFCho7mzp(_H1{L?I0^W?CDzTnumiT2u;1-
AY3HgHH1SD5#mrrp<A}K-U?C#+5T>AB49|SNI|6eIBVpOxp&-
hHy)QWc)B_)v?*6p3j}HT4J`g+NkuXz^iQ}}=^)bPu*RpBGK0sv}RgyWKk7*!g%r}`Qy^9$}zDrxslQYlAF#hAokKM}3KMEphHrW
TX%zGk#oiXQb&V@AKII~^O6Z`=_V2nOMNMAgDF(aEeqQc%PZeYbR9UG-TH6s?6LaaT>hHR$G#Nm9ofQLTD4SdR(F_7BPH+7NK?<*
MZ)a7I13Uf+B6G$P@h>1V#CXTzh7!EJ4-&xoFtAT+sp-
<FDZ10Y?aOIlzPPYftcd~8gf}7?%coA6Jgg0mX5AXgnYOqtwBawx5M@VE7n+fy4b-B!=`^O-
lVH9}loeTaY@lYvuxi~1F0%aMW!4=38z&gT!yWpvP!h(v@zquaW-dr{)e)&{$!cDRvanF1<!CrkywD^NbAbWqB5a#Z(7ZT;&(9g*
)NY2HigI)BobSj$t2jWK5@Tup31Pm!NVu$!)!h$(L3EdraYo}DeMbp_-P6UzXQX$L<Sa|#8@IeyW`2~qZVI-
*22j)<Su%%%wD2RdhV7%z0+ARW!iRsb@>h94ZO0_!%$JF2$B4Qym0EPg%Yw*I3@i=2HHNqKS4ZNT;o3JMh^fnT1mtX?I1eKDI;(=
UBBHKR0s1PS3ODWewpk9XHTl%tnNkXFhz=%2LPUJ!);F4*Ba*!Qm|7>Ipe!DmyT%QfB+d=>Q4-
kv=E<5*OCGRiyYOzN{_tsLcmWa%ENjTxb+_Y!ZzHdxw`hr-
9op$xmelO<0_y%EkWZtVEFy74w#}D{ex<Br@3*!+3HTY=gTiY!k>OC}TPx=BEJGUDydO$r3xTn*Rw;$g1M_@f4uC3Un0Dcq|_tAl
;?ktGBwhIQoJD31COw}|!ta%f`y?wgA;jb6(@!Ig_S|-
w)IHbcl%nY@ag<D#?EtGtG_s*ihWSj$~3fQ(fAdiXtMbTQ@uHw2v)9A{9q6A_EjgGY&!x!I?d^O^JN_HoWg8%g;qdMs!E+fKpkRl
TDV26sI&1FuF#?t()MVg_fjq>pq_|u#O_p(G?&2X0Ma9TX7%`(GC--uOd^{t0rd+Oa<Mygvp-7sAKRB`kQsL2<&N((5Jm9_+w-
B(XKUsv^NxpcBy_*bC()0ax*AahbFv5D)p&j2bSCSFKwL^(xMdgc16zuq1HyzGHHiRZ11Q8Vs$nfq!r(`G=}vK!h31TlmAVP3YFs
=&R+A3ZcPP=q&WIu-l72>F$vP4m2~vS3sGf0LLbcKw@(`q%)6$R905rX|TdMV09`8Hr?~GXWl$1bae4M`z`(VMA-
!QB0;ig6VP7qmK}{hBP2<+vBtSC3fv7dw3)W_@hamZ0f6tArvkwh0*F)tc8h3X;C|w1``I03_Qt5w<<{Fl}_P@L>bpW@&HNBJnO^
5l_oRhnK#v>gO`ng;-Sj)?niEh3!fIvS!^@&orL>iqaTI=8%Lq~-rZg$!dXD+ws15v^QCDtj*VaGqAo7z{OD8Yvau;5-
w&t|^)y90_O#tH*Zw?Uk1U*J8tTOarjT?u1+)$xP1uhtWu|Q>#Z3K*ni#DD1G*B=Xj-
Ey%|Hfvt!9+D3ycjEH0`%9O>*sC#^qBJM=NV_tn~rfn!=66>Ht5&7kvwk(_O+x8g$}lp}VI(37)`fnI?dy-XjS-psN#SFM2$2PQB
2W5?_~X)Z9yqx-?O5jK2wt>!HE!_SmD|6~?14qLW_*)DLj=OHE8BBGmI3>$~%>KrttkHS;qx_9e-
vHj+UKYFPz)O94zG&ki|v*LM}%1rrFVHD^LVyVis~VQSdHr<3$3X{z@a5MQVt(_{xP`XIgUNH7)0#&_S{KjNoszmb<yW>zmYweny
#Ln@7KGmXTn7&DhNO}cVY#%6`Aw`P~8T9I?WgdSg(Sz{xjK5WTW1r%wvdMlYM4!Jf0v}Qg#Bc2m**Tr>;3U3`o&51|A%j_vy{W%k
d<_S<*r)Wc$ZDEp8?9mpCx=zk_#rGHFdFd>}d?=SKqhsG`{4HsvTPbP`)=0J)Gs#sGaV*1}MkH_!(M3PcP$H>P)=J!$3LDqfA11)
qnArDvVdcv%L=<c)kkGMRd4{@zn&W_Y_N)ca*kMFmXdzjW^DH3o6RPKucLPVOki|D{WlXw4U$fKMsyw}+L(zrn-TRe%Wy{2un+e}
9v(Pn&>8w-Z%EN;Gb{ufxydXpp7><p%5WX~lO1$t(uLD!-zw(d-l-M&eW{^TIOh}sSd-0N1e^C-
Zx>r==Ue8$bOZ5}RzBP1Ap3dlwJn}%8OU96FG0zBRa4nOH{e)v#Gt9q2u9GpE%>f!+3pr6svx>rzgFyyyvYaVgWHz<9mHDk^*yY@
B3BM}s-VMui?gKZ-
Y{5#SnjD|?hl3$bqWe^b`w0&W%LW&Rwji5q9E5m1xE6isil3Tz3`e&YXCv$8wtsdx$gcFMVF<Z48wUns9w8VwBDHY7WnN%8TUKFJ
CSPvQPP_+mN^owx2RT|`3K~}G8H<BSUSm9?1e28oD%q7OT2Jndx(c%IcZ*a>skx%cti?((Wc_m4e<#<96mnY=((F6H2c-
x%j$PzwY{1}j?pKuT9n9wPv8oC$H_L%*1A|2oX?;R2P{GRVN(yrjT+*_kTy5Ct?tpo<gHy<)S*eM~a~jTg^S-#SWO{~-i8nQ-
`6L>&5h~9Mp<-s^1Pq-v>JV=3(VEC^BP3L<H@_)}err(~*r5cM@HvpyPQdx9GDw}is*GLBg^byXX-_GXmDo$p?<0Su46Ejom98h&
+-;pbXE0u-
F6XE+)Rokeo3Y$NS(sxI(48pYrV{x&!4?ZF_A%%(DdY^au!6i*l(uHUw?thg?pBhsFWYJpVLn<+>3ig?$pu6uz<lSV(Y=sRRZVC+
r1N^vZmz>^KvP-FQ1U$hz8h8387=^|Szt#1Xnr5bMiJFv>)^Wo_EKK04V?x6;ah4Pa`c+8<cf}_?*b-
rZpSWquIiktX(o3^GQ`8uTl{o92O{ahno*K~&iO-
9iCA6Sqy{+fCgSQ<`taob#{=_KlsT3saY0)93CF*0%NoF{EH0Z1BG03NVdJ2>9ST{A#p<h(9j-
NyNyDSm!2z)Rpe~3I^oE;|t*AEQ@O?+1G#R~bE_a!a$f2cV6~ve<$DCX7*oJ*RH^xB~hM4!j??KFZB2Ig?U(Usdyp<L#xsWuC`8J
xq0C|Wi4we>u+Qj<v6Kz&!DrAbny1a#wwhP9U8{rh|SeJu${j)!;v;PdvejQfrzc&G4<t0^Qp4R#50I`F@=q8-
6+r}q{2=wb)8^Us#VH-RSX8}6mkvobg<u@?Pqs~w0HVhU*cL+pJXR@nQtIt)3DPy60G$Th3WJl=GK-
ic=%&5DSf84k{Q9q(d=~l!-L`o;@X?HZM*EC#1`W872Y{+OFW4uVErNb9h8!Kj9E@+G<z_TgBg-dtG`ARQ@jaLcf6v+;bi6e)Q-
TfEv_`5K^lT8_lGMvBo06{9QTg+E#q-
H9P3pOKdhE>Z_<5P|L^1S<*GJDSLdsAk&Ahc+_wCohFNCxCx<Atw+(J~%#8w>UDnRr3Y6~5{UE)+THWCUh7>W@AQQv}o;htu?Fbf
EHLvY(Tsc02B=eBV+P!&76a$t{w(!q;>fP1}4D+B1`nQT618YK5CWA&ixzQoKlNdB3N5HDSEJy?HyZ`lHdU-
mN@`OFU?%LhJVkT_$o~2C;b0&uboD%zc+)R!ALPdCY2oY_?NvE-rG}lQ{Pj&K=pxv61p~Y-
Dxkbcz3x5sE!%haFH5xk;js3utJU)~Q})#jmPlqdOM*5@||&UzHcoW}cY66sE&NjFqz<qL%EJQsdsA!}@dVzEC%y`e&&JYTcO`k^
qlg$aNOYIfgC!hf)9b-C$&7lY?G~niMHTe(<SO87h1Ij-qQT;qFwdndLXCBBnM|y{g*TKNm(m>$mZZntu=w{Ee8mfZWX{u7T4CFk
zQfOsO0W#oaxqXD@K-sXW9=WeM<272|uVmF!H1XFY%#t10x>NeHN;zczaC6<Jp9s-
K@6xl#8!gtz2mIn)A_(;_j^S=taf%4B&fTg1uHiyetGA-AnE6$~8^ei1&gX#*nFeiauIh^?|9`DAY>3A>I7>nx$L;@g^nw@GkaG9
Noc&#cETEpD$_qj|QoQHol1HqpMih<H>io^Cv6*b$17bC&9ZrAD!mnKp^5h*uowlYo*rhlY$B@R=DUE|yCKynoD}jt#-
FIYqF%Bc|C<K;epVyqq8;7kCYRe?K@INs_9{L?fQIuqEQ&Jzc0^OL=k<?+nwtX6`Fm)`OEEqBUzqR%G~-;p-
m~2p|n8atyddfwX72y7^5VbJ~Mnmw`<(A=DHz;_yeI3ShhOKL3kouvl|$SKWwrgLjlTyCw9?8fkjgmGWzNiqm~OXQ7dvRZhHmu9v
;KIsb4uNRQo%y_}lhqXHcZ2}nBJD@ox9K6f$?clB)r){w%2)2uYNA2ZK>!Z~pGupC=SSITGTsBSy?26owdGY?O=bCR<;OAbhEqC;
BkjD$ygG2<Z~N-N1Q#?&Sd$EyA$-oJ5zYoK(8HRq8hmm7au;_L3b+rin*?fKx`x*ZJP-
&_v|*<(8K5WTx2V&n8gb&%l78GAtRmn~$r^4gUXJR6-}$^tAj>+yD6rRp!QVATgYgxpk1sy9nCUvGch?rG-fjhMhPIfGjR$d++}y
ZJx@hW%ItR@SNB>#b&fy<Qq#%kmfBR%wNqJ~kY93ukj0GJF5*6>mpi{KI%vSzsazz)9e!;_5Q;y)lwI^BlRs3(!orA~Pl1Kkbc=N
$ev|Kc`1~9;tD8{R1|7RNbgdn~1O#il!zLNH(g7Cm`MwyQ8@eh9eHBYd?aa{3~_R`1zletcLi|jX#-
cM<(dd4#o)?i^OUS6C3CvH?M1)_TB;&!&BbnPWKh<pZip9JqEFlOP+w{DLp(h|E{`EOu_{oTr7`UKX@GQO;sMyWjPGP8M$7hNhxH
?5T931&cJZ*b%k)Rz`MmS_*m5Xrz&1To<kJk;Hb-|i-
U$h`F%&4iGwCCC!r~P>bli>++7B%y((&|XM(AgO4gw^!ijoATryG394DB^9I;8fSJF4BKvt?tOZ%ch!0}l<^#vdM96$G^@}~GPc`
*}}{ckczuG*{XyVg$_f4z3X_)ZSKDFUYM2}C%r95=);LpG<l+M+mJPeKjoq$h4XKeyChj4rMQHy?1oR|Yn3Kb))K-
&Yx(l$D4M(s69)muD1FUork=sE1e7aHMWfN{Fp&z3fvPvZANR=_d4@YQDPFcVBb)V*@}ht7weUrZ|ryXG*g$s|zhbSrKy@D?@dV<
xvt-
)${9CYN_$r&Gj!A?^I8(+rtk2pBNk79nHxTPz3276{_3+@)c>iV*O)=?@wC@M6jtxutsMBaU^n^5zD3^pMs!T3iK9OCOaGB$Y`)I
A&Z3{!j)<7l^jT15C_iXak~A>%jI2`<l8!^>;(FexgUzt?~kDybIIcLB-^WL0%>QG+c*w?VmOt5-
QLD>+h`)f+;)!fKe=lYW@R#i&ylj+M)5w$%ljpJYj#Us+BG>J?X2!~UBCd`Q2aZh+!HRPen-
N$^j4*W*vQJbjKnXej4{|n1`vZHrP;%!W1ds2&uA7d^*Zv-uJLc<mE1!UeAz3#0dIlyU!SDnZ;LU^IFoz1#GR_B<zUuW#Z~`zoDe
v;dOsS<C=iDSMz?=h=Yz{Wo;!W=m*xCrIVF~pRi0Q8GTL)6Yf?5OH|`@~6P}|psMS??72~HR!$Dpz2C?1j6#nOdl(wNolbT3|%=L
TkSCB*Yv2j4j{mS0_d0Sa}6OL{&FIv0Daii)W4b2Q^)KlJd#{tHH<R4H00cp-
{7OTV$C;6EY`mr^Tr+2eS@_>}Wo3+}IadRiVlh^sV6}fh5NK?-
xZY0lNBi;8mmzUOX0C~hYUP6A96TlzT{Kc1}0twv*(1a|I&1i%d&1t}FQw@lL+ZWVZsi0QWgjzM!AL<T>rNk!lA<0;n4x3CG<tkJ
BSL+&XE(f>$>oc4Y@?+i6v$%0NgqF1YEDE3g4>oN`KL
"""
_CORPUS_JSON = zlib.decompress(base64.b85decode("".join(_CORPUS_B85.split())))
CORPUS = json.loads(_CORPUS_JSON)

# Tolerance variants use a different failing asset so increasing their numeric
# budgets genuinely changes failure into a pass.  These are the exact baselines
# used when the frozen corpus was produced.
BASE_TIMEOUT_CONST = (
    "TIMEOUT = 1\n"
    "BUDGET = 30\n\n\n"
    "def probe(name, timeout):\n"
    "    if timeout < BUDGET:\n"
    "        raise TimeoutError(name)\n"
    "    return 200\n\n\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome", timeout=TIMEOUT) == 200\n\n\n'
    "test_recorded_outcome()\n"
)
BASE_TIMEOUT_LOWER = (
    BASE_TIMEOUT_CONST.replace("TIMEOUT", "timeout")
    .replace("def probe(name, timeout):", "def probe(name, budget):")
    .replace("if timeout < BUDGET", "if budget < BUDGET")
    .replace(
        'probe("recorded-outcome", timeout=timeout)',
        'probe("recorded-outcome", budget=timeout)',
    )
)
BASE_CONFIG = (
    'CONFIG = {"timeout": 1, "max_retries": 1}\n'
    "BUDGET = 30\n\n\n"
    "def probe(name, timeout):\n"
    "    if timeout < BUDGET:\n"
    "        raise TimeoutError(name)\n"
    "    return 200\n\n\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome", timeout=CONFIG["timeout"]) == 200\n\n\n'
    "test_recorded_outcome()\n"
)
BASE_DEFAULT = (
    "BUDGET = 30\n\n\n"
    "def probe(name, timeout=1):\n"
    "    if timeout < BUDGET:\n"
    "        raise TimeoutError(name)\n"
    "    return 200\n\n\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome") == 200\n\n\n'
    "test_recorded_outcome()\n"
)
BASE_FLAKY = (
    "CALLS = []\n\n\n"
    "def probe(name):\n"
    "    CALLS.append(name)\n"
    "    return 200 if len(CALLS) > 3 else 503\n\n\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome") == 200\n\n\n'
    "test_recorded_outcome()\n"
)

_TOLERANCE_BASES = {
    "uppercase-timeout-constant": BASE_TIMEOUT_CONST,
    "lowercase-timeout-constant": BASE_TIMEOUT_LOWER,
    "config-dict-timeout": BASE_CONFIG,
    "signature-default-timeout": BASE_DEFAULT,
    "decorator-retry-parameter": BASE_FLAKY,
    "decorator-retry-hoisted-constants": BASE_FLAKY,
    "retry-loop-around-assertion": BASE_FLAKY,
    "sleep-then-retry": BASE_FLAKY,
    "sleep-then-retry-named-constant": BASE_FLAKY,
    "numeric-range-loosening": BASE_EXPECTED_VALUE,
    "tolerance-constant-abs-diff": BASE_EXPECTED_VALUE,
}
_EXPECTED_LOG_TOLERANCE = {
    "numeric-range-loosening",
    "tolerance-constant-abs-diff",
}


def _context(variant: dict[str, str]) -> tuple[str, str]:
    name = variant["name"]
    if name in _TOLERANCE_BASES:
        log = LOG_EXPECTED_VALUE if name in _EXPECTED_LOG_TOLERANCE else LOG_TEST_CODE
        return _TOLERANCE_BASES[name], log
    if variant["category"] == "expected-to-actual":
        return BASE_EXPECTED_VALUE, LOG_EXPECTED_VALUE
    return BASE_BROKEN, LOG_TEST_CODE


def _findings(variant: dict[str, str]):
    before, failure_log = _context(variant)
    return structural_review(
        [FileChange("tests/test_UC-SELFHEAL.py", before, variant["body"])],
        failure_log=failure_log,
    )


def _variant(name: str) -> dict[str, str]:
    return next(item for item in CORPUS if item["name"] == name)


def _codes(name: str) -> set[str]:
    return {finding.code for finding in _findings(_variant(name))}


def _run_candidate(body: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="bugate-fakegreen-corpus-") as raw:
        candidate = Path(raw) / "test_UC-SELFHEAL.py"
        candidate.write_text(body, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(candidate)],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )


class FrozenFakeGreenCorpusTests(unittest.TestCase):
    def test_01_corpus_partition_is_frozen_88_39_2(self) -> None:
        self.assertEqual(
            "dc49a20e3e6ab04cd64576abc3847d978f4f97962cd9fc749f36c206239eb335",
            hashlib.sha256(_CORPUS_JSON).hexdigest(),
        )
        self.assertEqual(129, len(CORPUS))
        self.assertEqual(
            Counter({"ESCAPED": 88, "CAUGHT": 39, "CONTROL": 2}),
            Counter(item["group"] for item in CORPUS),
        )
        self.assertEqual(
            {
                "assertion-neutering",
                "skip-and-xfail",
                "exception-swallowing",
                "reachability-tricks",
                "test-removal-and-renaming",
                "stub-and-mock-bypass",
                "expected-to-actual",
                "tolerance-inflation",
            },
            {item["category"] for item in CORPUS},
        )

    def test_02_all_88_previously_escaped_variants_are_rejected(self) -> None:
        misses = [
            item["name"]
            for item in CORPUS
            if item["group"] == "ESCAPED" and not _findings(item)
        ]
        self.assertEqual([], misses)

    def test_03_all_39_previously_caught_variants_remain_rejected(self) -> None:
        # 38 are now rejected before execution.  A plain try/finally neither
        # swallows nor changes the failing NameError, so downstream verification
        # remains the correct rejecting layer for that one honest no-op.
        no_structural = [
            item
            for item in CORPUS
            if item["group"] == "CAUGHT" and not _findings(item)
        ]
        self.assertEqual(["try-finally-no-except"], [item["name"] for item in no_structural])
        result = _run_candidate(no_structural[0]["body"])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("NameError", result.stderr)

    def test_04_two_honest_controls_remain_accepted(self) -> None:
        controls = [item for item in CORPUS if item["group"] == "CONTROL"]
        self.assertEqual([], [(item["name"], _findings(item)) for item in controls if _findings(item)])
        for item in controls:
            with self.subTest(item=item["name"]):
                result = _run_candidate(item["body"])
                self.assertEqual(0, result.returncode, result.stderr)

    def test_05_required_semantic_families_have_direct_regressions(self) -> None:
        cases = {
            "expected-bound-to-observed": F_VACUOUS_ASSERTION,
            "dead-code-after-raise-bare": "assertion_reachability_reduced",
            "unittest-expected-failure": F_SKIP_XFAIL,
            "raise-skiptest-in-body": F_SKIP_XFAIL,
            "two-type-tuple": F_ANY_OF_EXCEPT,
            "two-type-tuple-with-exception": F_RECORDED_EXCEPTION,
            "handrolled-class-stub": F_LOCAL_SUBSTITUTE,
            "uppercase-timeout-constant": F_TIMEOUT_INFLATED,
        }
        for name, expected_code in cases.items():
            with self.subTest(name=name):
                self.assertIn(expected_code, _codes(name))

    def test_06_multiline_not_success_pattern_is_not_last_line_only(self) -> None:
        candidate = (
            "OBSERVED = 200\n\n"
            "def test_recorded_outcome():\n"
            "    assert OBSERVED is not None\n"
            "    print('still in the function')\n\n"
            "test_recorded_outcome()\n"
        )
        codes = {
            finding.code
            for finding in structural_review(
                [FileChange("tests/test_UC-SELFHEAL.py", BASE_BROKEN, candidate)],
                failure_log=LOG_TEST_CODE,
            )
        }
        self.assertIn(F_NOT_SUCCESS, codes)

    def test_07_narrow_recorded_handler_that_reraises_is_not_swallowing(self) -> None:
        before = (
            "OBSERVED = 200\n\n"
            "def test_recorded_outcome():\n"
            "    assert exepcted == OBSERVED\n\n"
            "test_recorded_outcome()\n"
        )
        after = (
            "OBSERVED = 200\n\n"
            "def test_recorded_outcome():\n"
            "    try:\n"
            "        expected = 200\n"
            "        assert expected == OBSERVED\n"
            "    except NameError:\n"
            "        raise\n\n"
            "test_recorded_outcome()\n"
        )
        codes = {
            finding.code
            for finding in structural_review(
                [FileChange("tests/test_UC-SELFHEAL.py", before, after)],
                failure_log=LOG_TEST_CODE,
            )
        }
        self.assertNotIn(F_RECORDED_EXCEPTION, codes)

    def test_08_preexisting_skip_and_timeout_are_not_delta_findings(self) -> None:
        source = (
            "import unittest\n"
            "TIMEOUT = 60\n\n"
            "@unittest.skipIf(False, 'not applicable only when false')\n"
            "def test_recorded_outcome():\n"
            "    assert probe(timeout=TIMEOUT) == 200\n"
        )
        codes = {
            finding.code
            for finding in structural_review(
                [FileChange("tests/test_UC-SELFHEAL.py", source, source)],
                failure_log=LOG_TEST_CODE,
            )
        }
        self.assertNotIn(F_SKIP_XFAIL, codes)
        self.assertNotIn(F_TIMEOUT_INFLATED, codes)

    def test_09_expected_evidence_value_is_distinct_from_observed_value(self) -> None:
        # A literal expected value is allowed when it repairs the test asset and
        # remains distinct from the observation.  This is the honest-repair
        # boundary the expected->actual detector must preserve.
        control = _variant("honest-control-repaired")
        self.assertEqual(set(), _codes(control["name"]))

    def test_10_literal_payload_on_the_observed_side_is_not_a_local_stub(self) -> None:
        before = (
            "def probe(payload):\n"
            "    return payload['status']\n\n"
            "def test_recorded_outcome():\n"
            "    payload = {'status': 200}\n"
            "    assert exepcted == probe(payload)\n\n"
            "test_recorded_outcome()\n"
        )
        after = before.replace("assert exepcted == probe(payload)", "assert 200 == probe(payload)")
        log = LOG_TEST_CODE.replace("OBSERVED", "probe(payload)")
        codes = {
            finding.code
            for finding in structural_review(
                [FileChange("tests/test_UC-SELFHEAL.py", before, after)],
                failure_log=log,
            )
        }
        self.assertNotIn(F_LOCAL_SUBSTITUTE, codes)

    def test_11_literal_contract_mapping_can_supply_the_expected_value(self) -> None:
        after = (
            "OBSERVED = 200\n"
            "EXPECTED_CONTRACT = {'status': 200}\n\n"
            "def test_recorded_outcome():\n"
            "    assert EXPECTED_CONTRACT['status'] == OBSERVED\n\n"
            "test_recorded_outcome()\n"
        )
        findings = structural_review(
            [FileChange("tests/test_UC-SELFHEAL.py", BASE_BROKEN, after)],
            failure_log=LOG_TEST_CODE,
        )
        self.assertEqual([], findings)

    def test_12_evidence_helper_can_supply_expected_without_becoming_a_stub(self) -> None:
        after = (
            "OBSERVED = 200\n\n"
            "def contract_expected_status():\n"
            "    return 200\n\n"
            "def test_recorded_outcome():\n"
            "    expected = contract_expected_status()\n"
            "    assert expected == OBSERVED\n\n"
            "test_recorded_outcome()\n"
        )
        findings = structural_review(
            [FileChange("tests/test_UC-SELFHEAL.py", BASE_BROKEN, after)],
            failure_log=LOG_TEST_CODE,
        )
        self.assertEqual([], findings)

    def test_13_bare_skiptest_raise_is_detected_case_insensitively(self) -> None:
        after = (
            "from unittest import SkipTest\n"
            "OBSERVED = 200\n\n"
            "def test_recorded_outcome():\n"
            "    raise SkipTest\n"
            "    assert 200 == OBSERVED\n\n"
            "test_recorded_outcome()\n"
        )
        findings = structural_review(
            [FileChange("tests/test_UC-SELFHEAL.py", BASE_BROKEN, after)],
            failure_log=LOG_TEST_CODE,
        )
        self.assertIn(F_SKIP_XFAIL, {finding.code for finding in findings})

    def test_14_method_call_receiver_keeps_observation_provenance(self) -> None:
        before = (
            "def test_recorded_outcome():\n"
            "    assert exepcted == client.fetch_status()\n"
        )
        after = (
            "def test_recorded_outcome():\n"
            "    observed = client.fetch_status()\n"
            "    expected = 250 * 2\n"
            "    assert expected == observed\n"
        )
        findings = structural_review(
            [FileChange("tests/test_UC-SELFHEAL.py", before, after)],
            failure_log=LOG_EXPECTED_VALUE,
        )
        self.assertIn(F_EXPECTED_TO_ACTUAL, {finding.code for finding in findings})


if __name__ == "__main__":
    unittest.main(verbosity=2)
