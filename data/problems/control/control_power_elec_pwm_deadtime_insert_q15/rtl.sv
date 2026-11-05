module control_power_elec_pwm_deadtime_insert_q15 (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_deadtime_ns, in_core_clock_hz,
    input logic out_ready,
    output logic out_valid,
    output logic [7:0] out_dtg,
    output logic [9:0] out_ticks,
    output logic out_saturated, out_error
);
    logic [63:0] product, payload;
    logic [7:0] next_out_dtg;
    logic [9:0] next_out_ticks;
    logic next_out_saturated, next_out_error;
    always_comb begin
        product = {32'd0,in_deadtime_ns} * {32'd0,in_core_clock_hz};
        payload=0;next_out_saturated=0;next_out_error=(in_core_clock_hz==0);
        if(product<=64'd127000000000) next_out_dtg=product/64'd1000000000;
        else if(product<=64'd254000000000) begin
            if(product>=64'd128000000000) payload=(product-64'd128000000000)/64'd2000000000;
            next_out_dtg=8'h80 | payload[7:0];
        end else if(product<=64'd504000000000) begin
            if(product>=64'd256000000000) payload=(product-64'd256000000000)/64'd8000000000;
            next_out_dtg=8'hc0 | payload[7:0];
        end else if(product<=64'd1008000000000) begin
            if(product>=64'd512000000000) payload=(product-64'd512000000000)/64'd16000000000;
            next_out_dtg=8'he0 | payload[7:0];
        end else begin next_out_dtg=8'hff;next_out_saturated=1;end
        if(next_out_dtg<128) next_out_ticks={2'd0,next_out_dtg};
        else if(next_out_dtg<192) next_out_ticks=(10'd64+{4'd0,next_out_dtg[5:0]})<<1;
        else if(next_out_dtg<224) next_out_ticks=(10'd32+{5'd0,next_out_dtg[4:0]})<<3;
        else next_out_ticks=(10'd32+{5'd0,next_out_dtg[4:0]})<<4;
        if(next_out_error) begin next_out_dtg=0;next_out_ticks=0;next_out_saturated=0;end
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_dtg <= 0; out_ticks <= 0; out_saturated <= 0; out_error <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_dtg <= next_out_dtg; out_ticks <= next_out_ticks; out_saturated <= next_out_saturated; out_error <= next_out_error; end
            else begin out_dtg <= 0; out_ticks <= 0; out_saturated <= 0; out_error <= 0; end
        end
    end
endmodule
