/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Process.hpp"
#include "RNG.hpp"
#include "common.hpp"
#include "GBMValuationModel.hpp"
#include "taosim/simulation/ISimulation.hpp"
#include <spdlog/spdlog.h>

#include <boost/circular_buffer.hpp>
#include <boost/accumulators/accumulators.hpp>
#include <boost/accumulators/statistics/stats.hpp>
#include <boost/accumulators/statistics/variance.hpp>

#include <pugixml.hpp>

//-------------------------------------------------------------------------

class Simulation;

// struct TimestampedHistory
// {
//     Timestamp timestamp;
//     double price;
//     double logReturn;
//     // Plan here is to pass durations in order to correctly, but this might be needed for other than ST as well
//     // If others are fine with static last, we do not need to push but change from previous
//     Timestamp delay;
//     float psi;
// };

// struct GBMdesc {
//     double S0; 
//     double mu; 
//     double sigma;
//     uint64_t seed;
// };
struct DurationComp
{
    float delay;
    float psi;
};
//-------------------------------------------------------------------------
struct MagneticFieldDesc
{
    uint32_t num_agents_sqrt;
    float alpha;
    float beta;
    float interactionCoef;
    uint64_t seed;
    fs::path filepath;
    Timestamp updatePeriod;
};
//-------------------------------------------------------------------------
class MagneticFieldLogger
{
public:
    MagneticFieldLogger(const fs::path& filepath);

    [[nodiscard]] const fs::path& filepath() const noexcept { return m_filepath; }

    void log(Timestamp timestamp, float totalMagnetism, const std::vector<int> field, uint32_t lastPosition = 0);
    
private:
    fs::path m_filepath;
    std::unique_ptr<spdlog::logger> m_logger;
    static constexpr std::string_view s_header = "time,total,field,lastPosition"; 
};

//-------------------------------------------------------------------------

class MagneticField : public Process
{
public:
    MagneticField() noexcept = default;
    explicit MagneticField(taosim::simulation::ISimulation* simulation, const MagneticFieldDesc& desc) noexcept;

    [[nodiscard]] float magnetism() const noexcept { return m_magnetism; }
    [[nodiscard]] float magnetismReturn() const noexcept { return m_return; }
    [[nodiscard]] float avgMagnetism() const noexcept { return m_magnetism/ (float) m_numAgents; }
    [[nodiscard]] uint32_t rows() const noexcept { return m_rows; }
    [[nodiscard]] uint32_t numAgents() const noexcept { return m_numAgents; }
    [[nodiscard]] int sign_at(uint32_t id) const noexcept { return m_field[id]; }   
    
    [[nodiscard]] std::tuple<int,int> resolvePosition(uint32_t id) const noexcept { return {(int) id % m_rows, (int) id/m_rows}; } 
    void setValAt(uint32_t pos, int val);
    void insertDurationComp(const std::string& name, DurationComp event);
    DurationComp getDurationComp(const std::string& basename); 
    int asyncUpdate(uint32_t pos);
    void logState(Timestamp timestamp, uint32_t lastPosition = 0);

    virtual void update(Timestamp timestamp) override;
    virtual double value() const override { return m_value; }
    virtual uint64_t count() const override { return m_last_count; }
    virtual void checkpointSerialize(
        rapidjson::Document& json, const std::string& key = {}) const override;

    [[nodiscard]] static std::unique_ptr<MagneticField> fromXML(
        taosim::simulation::ISimulation* simulation, pugi::xml_node node, uint64_t seed, const fs::path& filepath);
    [[nodiscard]] static std::unique_ptr<MagneticField> fromCheckpoint(
        taosim::simulation::ISimulation* simulation, const rapidjson::Value& json, const fs::path& filepath);

private:
    taosim::simulation::ISimulation* m_simulation;
    RNG m_rng;
    double m_value; 
    uint64_t m_last_count = 0;
    uint64_t m_historySize = 0; 
    std::map<std::string, DurationComp> m_basenameDuration;

    double m_logReturn = 0.0;

    uint32_t m_rows;
    uint32_t m_numAgents;
    float m_magnetism;
    float m_return;
    float m_beta;
    float m_alpha;
    float m_J;
    std::vector<int> m_field;
    std::unique_ptr<MagneticFieldLogger> m_logger;
    float totalMagnetism();
    float updateMagnetism();
    float localSum(int x, int y, float J);

    // Easier mental access to cell
    int position(int x, int y) const { return x + y*m_rows; }
    int at_field(int x, int y) const {return m_field[x + y*m_rows];} 
    static int BoolToSpin(bool v) {return v ? +1 : -1;}

    void initialize() {
        m_magnetism = 0.0;
        for(int y=0; y<m_rows; y++) {
            for(int x=0; x<m_rows; x++) {
                m_field[x + y*m_rows] = BoolToSpin(std::bernoulli_distribution{0.5}(m_rng));
            }
        };
        m_magnetism = updateMagnetism();
        m_value = avgMagnetism();
        m_last_count= 0;
    }
};
