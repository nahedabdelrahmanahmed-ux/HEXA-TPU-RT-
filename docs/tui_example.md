# Real screens of mobius4-browser against the ACME CSE filled by committee_demo.py (captured with pyte, 150x45)

## start
```
oneM2M Resource Browser │ profile acme │ http://localhost:8080/cse-in │ fr CAdmin │ live off: no broker │ view: what CAdmin may discover
 ▼ cse-in  cb  17                                          │ <CSEBase>  cse-in
 ├── ▶ acpCreateRootResources  acp                         │  short  long name              value
 ├── ▶ acpRetrieveCSEBase  acp                             │  rn     resourceName           cse-in
 ├── ▶ CAdmin  ae                                          │  csi    CSE-ID                 /id-in
 ├── ▶ aviation-clear-sky-1794568664901  ae                │  srt    supportedResourceType  [1, 2, 3, 4, 5, 9, 10, 13, 14, 15, 16, 17, 18, 23, 24, …
 ├── ▶ aviation-dense-fog-approach-1794568664992  ae       │
 ├── ▶ aviation-light-haze-1794568664947  ae               │
 ├── ▶ aviation-skin-burst-anomaly-1794568666419  ae       │
 ├── ▶ aviation-skin-critical-event-1794568666901  ae      │
 ├── ▶ aviation-skin-gradual-anomaly-1794568665918  ae     │
 ├── ▶ aviation-skin-normal-1794568665420  ae              │
 ├── ▶ aviation-sudden-incursion-1794568665363  ae         │
 ├── ▶ water-daily-cycle-1794568667447  ae                 │
 ├── ▶ water-gradual-leak-1794568667491  ae                │
 ├── ▶ water-steady-demand-1794568667405  ae               │
 ├── ▶ water-sudden-burst-1794568667540  ae                │
 ├── ▶ defaultNTP  ntp                                     │
 └── ▶ AEContactList  alst                                 │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
                                                           │
```

## depth 3
```
oneM2M Resource Browser │ profile acme │ http://localhost:8080/cse-in │ fr CAdmin │ live off: no broker │ view: what CAdmin may discover
 ▼ cse-in  cb  17                                          │ <CSEBase>  cse-in
 ├── ▼ acpCreateRootResources  acp  (empty)                │  short  long name              value
 ├── ▼ acpRetrieveCSEBase  acp  (empty)                    │  rn     resourceName           cse-in
 ├── ▼ CAdmin  ae  (empty)                                 │  csi    CSE-ID                 /id-in
 ├── ▼ aviation-clear-sky-1794568664901  ae  2             │  srt    supportedResourceType  [1, 2, 3, 4, 5, 9, 10, 13, 14, 15, 16, 17, 18, 23, 24, …
 │   ├── ▼ reflexEvents  cnt  8                         ▂▂ │
 │   │   ├── la  ·latest                                   │
 │   │   ├── ol  ·oldest                                   │
 │   │   ├── ▶ cin_0rykUGcd7l  cin                         │
 │   │   ├── ▶ cin_6NtBesd4Jl  cin                         │
 │   │   ├── ▶ cin_Ak0qP9BCN8  cin                         │
 │   │   ├── ▶ cin_dbjYedTj13  cin                         │
 │   │   ├── ▶ cin_P9x33wqPnP  cin                         │
 │   │   ├── ▶ cin_PAhrdaO1VT  cin                         │
 │   │   ├── ▶ cin_qgRmzAFqNi  cin                         │
 │   │   └── ▶ cin_ZoFP2jPrlI  cin                         │
 │   └── ▼ sensorData  cnt                                 │
 │       ├── la  ·latest                                   │
 │       └── ol  ·oldest                                   │
 ├── ▼ aviation-dense-fog-approach-1794568664992  ae  2    │
 │   ├── ▼ reflexEvents  cnt  18                           │
 │   │   ├── la  ·latest                                   │
 │   │   ├── ol  ·oldest                                   │
 │   │   ├── ▶ cin_2u10YHlYof  cin                         │
 │   │   ├── ▶ cin_531YvaETs7  cin                         │
 │   │   ├── ▶ cin_5aCsaKkeXB  cin                         │
 │   │   ├── ▶ cin_6FDAuwHCpw  cin                         │
 │   │   ├── ▶ cin_7OwqHc1eRX  cin                         │
 │   │   ├── ▶ cin_EJWqa9gQEK  cin                         │
 │   │   ├── ▶ cin_fX4FwoQZ1j  cin                         │
 │   │   ├── ▶ cin_geNCJpp7g7  cin                         │
 │   │   ├── ▶ cin_k8iYHHnm3q  cin                         │
 │   │   ├── ▶ cin_kDVw25qL7H  cin                         │
 │   │   ├── ▶ cin_ScnFetwY89  cin                         │
 │   │   ├── ▶ cin_T5Dt8LVtUH  cin                         │
```

## registrations (R)
```
oneM2M Resource Browser │ profile acme │ http://localhost:8080/cse-in │ fr CAdmin │ live off: no broker │ view: what CAdmin may discover
 ▼ cse-in  cb  17                                          │ <CSEBase>  cse-in
 ├── ▼ acpCreateRootResources  acp  (empty)                │  short  long name              value
 ├── ▼ acpRetrieveCSEBase  acp  (empty)                    │  rn     resourceName           cse-in
 ├── ▼ CAdmin  ae  (empty)                                 │  csi    CSE-ID                 /id-in
 ├── ▼ aviation-clear-sky-1794568664901  ae  2             │  srt    supportedResourceType  [1, 2, 3, 4, 5, 9, 10, 13, 14, 15, 16, 17, 18, 23, 24, …
 │   ├── ▼ reflexEvents  cnt  8                         ▂▂ │
 │   │   ├── la  ·latest                                   │
 │   │   ├── ol  ·oldest                                   │
 │   │   ├── ▶ cin_0rykUGcd7l  cin                         │
 │   │   ├── ▶ cin_6NtBesd4Jl  cin  ╭────────────────────────────────────────────────────────────────────────────╮
 │   │   ├── ▶ cin_Ak0qP9BCN8  cin  │                                                                            │
 │   │   ├── ▶ cin_dbjYedTj13  cin  │  13 AEs — what CAdmin may discover                                         │
 │   │   ├── ▶ cin_P9x33wqPnP  cin  │                                                                            │
 │   │   ├── ▶ cin_PAhrdaO1VT  cin  │   <AE>                                                                     │
 │   │   ├── ▶ cin_qgRmzAFqNi  cin  │   name                                         AE-ID        App-ID         │
 │   │   └── ▶ cin_ZoFP2jPrlI  cin  │   aviation-clear-sky-1794568664901             SBBTnWSBwCR  NAviationEdge  │
 │   └── ▼ sensorData  cnt          │   aviation-dense-fog-approach-1794568664992    STcx36kYV4n  NAviationEdge  │
 │       ├── la  ·latest            │   aviation-light-haze-1794568664947            SGuG6UMPJJV  NAviationEdge  │
 │       └── ol  ·oldest            │   aviation-skin-burst-anomaly-1794568666419    Sn6heELppCW  NBDO-SKIN-edg  │
 ├── ▼ aviation-dense-fog-approach-1│   aviation-skin-critical-event-1794568666901   Sf72gr2TIFL  NBDO-SKIN-edg  │
 │   ├── ▼ reflexEvents  cnt  18    │   aviation-skin-gradual-anomaly-1794568665918  SFdXKLS9M27  NBDO-SKIN-edg  │
 │   │   ├── la  ·latest            │   aviation-skin-normal-1794568665420           SajDteVXIIY  NBDO-SKIN-edg  │
 │   │   ├── ol  ·oldest            │   aviation-sudden-incursion-1794568665363      SaxLUBOGY94  NAviationEdge  │
 │   │   ├── ▶ cin_2u10YHlYof  cin  │   CAdmin                                       CAdmin       NCAdmin        │
 │   │   ├── ▶ cin_531YvaETs7  cin  │   water-daily-cycle-1794568667447              StrQsxmrppT  NWaterEdge     │
 │   │   ├── ▶ cin_5aCsaKkeXB  cin  │   water-gradual-leak-1794568667491             SnuN8aqYMYe  NWaterEdge     │
 │   │   ├── ▶ cin_6FDAuwHCpw  cin  │   water-steady-demand-1794568667405            SjXVpQ4BOYG  NWaterEdge     │
 │   │   ├── ▶ cin_7OwqHc1eRX  cin  │   water-sudden-burst-1794568667540             SnA8nvV3gft  NWaterEdge     │
 │   │   ├── ▶ cin_EJWqa9gQEK  cin  │                                                 ▎                          │
 │   │   ├── ▶ cin_fX4FwoQZ1j  cin  │                                                                            │
 │   │   ├── ▶ cin_geNCJpp7g7  cin  │  Enter opens it in the tree · Esc closes                                   │
 │   │   ├── ▶ cin_k8iYHHnm3q  cin  │                                                                            │
 │   │   ├── ▶ cin_kDVw25qL7H  cin  ╰────────────────────────────────────────────────────────────────────────────╯
 │   │   ├── ▶ cin_ScnFetwY89  cin                         │
 │   │   ├── ▶ cin_T5Dt8LVtUH  cin                         │
```

