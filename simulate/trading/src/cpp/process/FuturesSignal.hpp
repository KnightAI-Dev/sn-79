/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Process.hpp"
#include "RNG.hpp"
#include "common.hpp"
#include "taosim/simulation/ISimulation.hpp"

#include <pugixml.hpp>

//-------------------------------------------------------------------------

class Simulation;

//-------------------------------------------------------------------------

class FuturesSignal : public Process
{
public:
    FuturesSignal(
        taosim::simulation::ISimulation* simulation,
        uint64_t bookId,
        uint64_t seedInterval,
        double X0,
        Timestamp updatePeriod,
        float lambda) noexcept;

    virtual void update(Timestamp timestamp) override;
    virtual double value() const override {return m_value; } 
    double logReturn() {return m_logReturn; }
    double volumeFactor();
    virtual uint64_t count() const override { return m_last_count; }
    virtual void checkpointSerialize(
        rapidjson::Document& json, const std::string& key = {}) const override;

    [[nodiscard]] static std::unique_ptr<FuturesSignal> fromXML(
        taosim::simulation::ISimulation* simulation, pugi::xml_node node, uint64_t bookId, double X0);
    [[nodiscard]] static std::unique_ptr<FuturesSignal> fromCheckpoint(
        taosim::simulation::ISimulation* simulation, const rapidjson::Value& json, double X0);

private:
    taosim::simulation::ISimulation* m_simulation;
    uint64_t m_bookId;
    uint64_t m_seedInterval;
    std::string m_seedfile;
    double m_X0; 
    double m_value;
    double m_logReturn = 0.0;
    double m_volumeFactor = 2.0;
    uint32_t m_factorCounter = 0;
    float m_lambda;
    uint64_t m_last_count = 0;
    double m_last_seed = 0;
    Timestamp m_last_seed_time = 0;
};

//-------------------------------------------------------------------------
