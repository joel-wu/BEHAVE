module signal_processing_diffusion_sched_sched_lms_discrete (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_model_output,
    input  logic [31:0] in_timestep,
    input  logic [31:0] in_sample,
    input  logic [2:0]  in_order,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_prev_sample,
    output logic [31:0] out_pred_original_sample
);

    logic busy, initialized;
    logic [9:0] step_index;
    logic [2:0] count;
    logic signed [31:0] history [0:3];
    logic [9:0] index_c;
    logic [2:0] count_c, order_c;
    logic signed [63:0] sample_c, noise_c, sigma_c;
    logic signed [63:0] correction_c, predicted_c, previous_c;
    integer i;

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    always_comb begin
        index_c = initialized ? step_index : (in_timestep > 1000 ? 10'd1000 : in_timestep[9:0]);
        count_c = count < 4 ? count + 3'd1 : 3'd4;
        order_c = in_order < 1 ? 3'd1 : (in_order > 4 ? 3'd4 : in_order);
        if (order_c > count_c) order_c = count_c;
        sample_c = $signed(in_sample);
        noise_c = $signed(in_model_output);
        sigma_c = 64'sd1000 - $signed({1'b0,index_c});
        correction_c = 0;
        if (index_c < 1000) begin
            case (order_c)
                1: correction_c = -noise_c;
                2: correction_c = (-64'sd3*noise_c)/64'sd2 + $signed(history[0])/64'sd2;
                3: correction_c = (-64'sd23*noise_c)/64'sd12
                                  + (64'sd16*$signed(history[0]))/64'sd12
                                  - (64'sd5*$signed(history[1]))/64'sd12;
                default: correction_c = (-64'sd55*noise_c)/64'sd24
                                  + (64'sd59*$signed(history[0]))/64'sd24
                                  - (64'sd37*$signed(history[1]))/64'sd24
                                  + (64'sd9*$signed(history[2]))/64'sd24;
            endcase
        end
        previous_c = sample_c + correction_c;
        predicted_c = sample_c - sigma_c*noise_c;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            initialized <= 0;
            step_index <= 0;
            count <= 0;
            for (i=0; i<4; i=i+1) history[i] <= 0;
            out_prev_sample <= 0;
            out_pred_original_sample <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            initialized <= 1;
            step_index <= index_c;
            if (index_c < 1000) begin
                step_index <= index_c + 10'd1;
                count <= count_c;
                history[0] <= in_model_output;
                for (i=1; i<4; i=i+1) history[i] <= history[i-1];
            end
            out_prev_sample <= previous_c[31:0];
            out_pred_original_sample <= predicted_c[31:0];
        end else if (out_ready) busy <= 0;
    end
endmodule
