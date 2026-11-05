module control_imu_estimation_madgwick_ahrs_update (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_gx,
    input logic signed [15:0] in_gy,
    input logic signed [15:0] in_gz,
    input logic signed [15:0] in_ax,
    input logic signed [15:0] in_ay,
    input logic signed [15:0] in_az,
    input logic signed [15:0] in_dt,
    input logic signed [15:0] in_beta,
    input logic signed [15:0] in_acc_confidence_decay,
    output logic out_valid,
    input logic out_ready,
    output logic signed [15:0] out_q0,
    output logic signed [15:0] out_q1,
    output logic signed [15:0] out_q2,
    output logic signed [15:0] out_q3,
    output logic [15:0] out_accMagP
);
// Wide temporaries preserve the gradient and doubled gains. No early Q15 clamp.
function automatic longint signed mul(input longint signed a, b);
    mul = (a*b) >>> 15;
endfunction

function automatic longint signed clip(input longint signed v, lo, hi);
    clip = v < lo ? lo : (v > hi ? hi : v);
endfunction

function automatic longint signed root96(input logic [95:0] n);
    logic [47:0] root;
    logic [49:0] remainder, trial;
    root = 0;
    remainder = 0;
    for (int k=0; k<48; k=k+1) begin
        remainder = (remainder << 2) | {48'b0,n[94-2*k +: 2]};
        trial = ({2'b0,root} << 2) | 50'd1;
        root = root << 1;
        if (remainder >= trial) begin
            remainder = remainder - trial;
            root = root | 48'd1;
        end
    end
    root96 = $signed({16'b0,root});
endfunction

function automatic longint signed norm_den(input longint signed a,b,c,d);
    logic [95:0] sum;
    sum = 96'(a*a + b*b + c*c + d*d);
    norm_den = root96(sum << 32);
endfunction

function automatic longint signed unit(input longint signed v, denominator);
    if (denominator == 0) unit = 0;
    else unit = clip((v <<< 31) / denominator, -32768, 32767);
endfunction

logic pending;
assign in_ready = !rst && (!pending || out_ready);
assign out_valid = !rst && pending;
logic signed [15:0] state_q0;
logic signed [15:0] state_q1;
logic signed [15:0] state_q2;
logic signed [15:0] state_q3;
logic [15:0] state_accMagP;

always_ff @(posedge clk) begin
    longint signed a;
    longint signed acc2;
    longint signed accMagP;
    longint signed acc_confidence_decay;
    longint signed ax;
    longint signed ay;
    longint signed az;
    longint signed b;
    longint signed bc;
    longint signed beta;
    longint signed c;
    longint signed confidence;
    longint signed d0;
    longint signed d1;
    longint signed d2;
    longint signed d3;
    longint signed delta;
    longint signed den;
    longint signed dt;
    longint signed gx;
    longint signed gy;
    longint signed gz;
    longint signed n0;
    longint signed n1;
    longint signed n2;
    longint signed n3;
    longint signed q0;
    longint signed q00;
    longint signed q1;
    longint signed q11;
    longint signed q2;
    longint signed q22;
    longint signed q3;
    longint signed q33;
    longint signed s0;
    longint signed s1;
    longint signed s2;
    longint signed s3;
    if (rst) begin
        pending <= 0;
        state_q0 <= 16'd32767;
        state_q1 <= 16'd0;
        state_q2 <= 16'd0;
        state_q3 <= 16'd0;
        state_accMagP <= 16'd32768;
        out_q0 <= 16'd32767;
        out_q1 <= 16'd0;
        out_q2 <= 16'd0;
        out_q3 <= 16'd0;
        out_accMagP <= 16'd32768;
    end else if (in_valid && in_ready) begin
        q0 = state_q0;
        q1 = state_q1;
        q2 = state_q2;
        q3 = state_q3;
        accMagP = state_accMagP;
        gx = $signed(in_gx);
        gy = $signed(in_gy);
        gz = $signed(in_gz);
        ax = $signed(in_ax);
        ay = $signed(in_ay);
        az = $signed(in_az);
        dt = $signed(in_dt);
        beta = $signed(in_beta);
        acc_confidence_decay = $signed(in_acc_confidence_decay);
        d0 = ((((-mul(q1, gx)) - mul(q2, gy)) - mul(q3, gz)) >>> 64'sd1);
        d1 = (((mul(q0, gx) + mul(q2, gz)) - mul(q3, gy)) >>> 64'sd1);
        d2 = (((mul(q0, gy) - mul(q1, gz)) + mul(q3, gx)) >>> 64'sd1);
        d3 = (((mul(q0, gz) + mul(q1, gy)) - mul(q2, gx)) >>> 64'sd1);
        acc2 = (((ax * ax) + (ay * ay)) + (az * az));
        if (acc2 > 64'sd107374) begin
            accMagP = (((64'sd9 * accMagP) + root96(acc2)) / 64'sd10);
            delta = (accMagP - 64'sd32768);
            delta = ((delta < 64'sd0) ? (-delta) : delta);
            confidence = clip((64'sd32768 - mul(acc_confidence_decay, root96((delta <<< 64'sd15)))), 64'sd0, 64'sd32768);
            den = norm_den(ax, ay, az, 64'sd0);
            a = unit(ax, den);
            b = unit(ay, den);
            c = unit(az, den);
            q00 = mul(q0, q0);
            q11 = mul(q1, q1);
            q22 = mul(q2, q2);
            q33 = mul(q3, q3);
            s0 = (((mul((64'sd4 * q0), q22) + mul((64'sd2 * q2), a)) + mul((64'sd4 * q0), q11)) - mul((64'sd2 * q1), b));
            s1 = (((((((mul((64'sd4 * q1), q33) - mul((64'sd2 * q3), a)) + mul((64'sd4 * q00), q1)) - mul((64'sd2 * q0), b)) - (64'sd4 * q1)) + mul((64'sd8 * q1), q11)) + mul((64'sd8 * q1), q22)) + mul((64'sd4 * q1), c));
            s2 = (((((((mul((64'sd4 * q00), q2) + mul((64'sd2 * q0), a)) + mul((64'sd4 * q2), q33)) - mul((64'sd2 * q3), b)) - (64'sd4 * q2)) + mul((64'sd8 * q2), q11)) + mul((64'sd8 * q2), q22)) + mul((64'sd4 * q2), c));
            s3 = (((mul((64'sd4 * q11), q3) - mul((64'sd2 * q1), a)) + mul((64'sd4 * q22), q3)) - mul((64'sd2 * q2), b));
            den = norm_den(s0, s1, s2, s3);
            bc = mul(beta, confidence);
            d0 = (d0 - mul(bc, unit(s0, den)));
            d1 = (d1 - mul(bc, unit(s1, den)));
            d2 = (d2 - mul(bc, unit(s2, den)));
            d3 = (d3 - mul(bc, unit(s3, den)));
        end
        n0 = (q0 + mul(d0, dt));
        n1 = (q1 + mul(d1, dt));
        n2 = (q2 + mul(d2, dt));
        n3 = (q3 + mul(d3, dt));
        den = norm_den(n0, n1, n2, n3);
        q0 = ((den != 64'sd0) ? unit(n0, den) : 64'sd32767);
        q1 = unit(n1, den);
        q2 = unit(n2, den);
        q3 = unit(n3, den);
        state_q0 <= q0[15:0];
        state_q1 <= q1[15:0];
        state_q2 <= q2[15:0];
        state_q3 <= q3[15:0];
        state_accMagP <= accMagP[15:0];
        out_q0 <= q0[15:0];
        out_q1 <= q1[15:0];
        out_q2 <= q2[15:0];
        out_q3 <= q3[15:0];
        out_accMagP <= accMagP[15:0];
        pending <= 1;
    end else if (out_valid && out_ready) begin
        pending <= 0;
    end
end
endmodule
