package com.mibia.gatekeeper

interface Platform {
    val name: String
}

expect fun getPlatform(): Platform